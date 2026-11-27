using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace PracticeToolLauncher
{
    internal sealed class RuntimePackage
    {
        internal const string Version = "3.14.10";
        internal readonly string Architecture;
        internal readonly string Sha256;
        internal readonly Uri Download;

        internal RuntimePackage(string architecture, string hash, Uri download)
        {
            Architecture = architecture;
            Sha256 = hash;
            Download = download;
        }

        internal static RuntimePackage ForWindows()
        {
            ushort currentMachine;
            ushort platform = Environment.Is64BitOperatingSystem ? (ushort)0x8664 : (ushort)0x014c;
            try
            {
                ushort nativeMachine;
                if (IsWow64Process2(GetCurrentProcess(), out currentMachine, out nativeMachine))
                    platform = nativeMachine;
            }
            catch (EntryPointNotFoundException) { }
            string arch;
            string hash;
            switch (platform)
            {
                case 0x8664:
                    arch = "amd64";
                    hash = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159";
                    break;
                case 0xaa64:
                    arch = "arm64";
                    hash = "bbe116bb1248820359cbf25127ac5c7794ea161869d83686cfbc9a45203ff836";
                    break;
                case 0x014c:
                    arch = "win32";
                    hash = "11a28e8c00a6d8f56b0a6164d52546133a9ed015a5b33e04536917e4fd6e306c";
                    break;
                default:
                    throw new InvalidOperationException("无法识别 Windows 的系统架构。");
            }
            return new RuntimePackage(arch, hash,
                new Uri("https://www.python.org/ftp/python/" + Version + "/python-" + Version + "-embed-" + arch + ".zip"));
        }

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();

        [DllImport("kernel32.dll")]
        private static extern bool IsWow64Process2(IntPtr process, out ushort processMachine, out ushort nativeMachine);
    }

    internal sealed class InstallProgress
    {
        internal readonly string Message;
        internal readonly int Percent;

        internal InstallProgress(string message, int percent)
        {
            Message = message;
            Percent = percent;
        }
    }

    internal sealed class RuntimeInstaller
    {
        private const string Marker = ".verified";
        private const long DownloadLimit = 30L * 1024 * 1024;
        private const long ExpandedLimit = 100L * 1024 * 1024;
        private const string ManagedDirectory = "launcher-runtime";
        private readonly RuntimePackage package;
        private readonly string managed;
        private readonly string runtime;
        private readonly string appHash;
        private readonly byte[] appZip;
        private readonly Func<HttpMessageHandler> httpHandler;
        private readonly Func<CancellationToken, LocalPython> findLocalPython;
        internal readonly string AppDirectory;
        internal readonly string DataDirectory;

        internal RuntimeInstaller(string dataDirectory, RuntimePackage package, byte[] appZip,
            Func<HttpMessageHandler> httpHandler = null, Func<CancellationToken, LocalPython> findLocalPython = null)
        {
            DataDirectory = Path.GetFullPath(dataDirectory);
            this.package = package;
            this.appZip = appZip;
            this.httpHandler = httpHandler ?? (() => new HttpClientHandler { AllowAutoRedirect = false });
            this.findLocalPython = findLocalPython ?? LocalPython.Find;
            using (var app = new MemoryStream(appZip, false)) appHash = Hash(app);
            managed = Path.Combine(DataDirectory, ManagedDirectory);
            runtime = Path.Combine(managed, "python-" + RuntimePackage.Version + "-" + package.Architecture);
            AppDirectory = Path.Combine(managed, "app-" + appHash.Substring(0, 16));
        }

        internal static string DefaultDataDirectory()
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, Environment.SpecialFolderOption.DoNotVerify);
            if (!Path.IsPathRooted(local)) throw new IOException("Windows 未返回当前用户的应用数据目录。");
            return Path.Combine(local, "PracticeTool");
        }

        internal static RuntimeInstaller Create()
        {
            using (Stream embedded = Assembly.GetExecutingAssembly().GetManifestResourceStream("PracticeTool.service.zip"))
            using (var contents = new MemoryStream())
            {
                if (embedded == null) throw new InvalidOperationException("启动器缺少内置服务文件。");
                embedded.CopyTo(contents);
                return new RuntimeInstaller(DefaultDataDirectory(), RuntimePackage.ForWindows(), contents.ToArray());
            }
        }

        internal string PythonPath { get; private set; }
        internal string PythonDescription { get; private set; }

        internal bool HasFiles
        {
            get { return Directory.Exists(managed) || File.Exists(Path.Combine(DataDirectory, "practice.sqlite3")); }
        }

        internal async Task PrepareAsync(IProgress<InstallProgress> progress, CancellationToken cancel)
        {
            PythonPath = null;
            PythonDescription = null;
            EnsureOrdinary(DataDirectory);
            EnsureOrdinary(managed);
            EnsureOrdinary(AppDirectory);

            progress.Report(new InstallProgress("正在查找本机 Python", -1));
            LocalPython local = findLocalPython(cancel);
            cancel.ThrowIfCancellationRequested();
            Directory.CreateDirectory(managed);
            if (local == null)
            {
                await PrepareManagedPythonAsync(progress, cancel).ConfigureAwait(false);
            }

            if (!Complete(AppDirectory, appHash, "practice_tool/managed_service.py", "practice_tool/web_assets/dashboard.html"))
            {
                progress.Report(new InstallProgress("正在准备签到服务", 95));
                using (var file = new MemoryStream(appZip, false)) InstallZip(file, AppDirectory, appHash, cancel);
            }

            PythonPath = local != null ? local.Path : Path.Combine(runtime, "python.exe");
            PythonDescription = local != null ? "Python " + local.Version + " · 本机环境"
                : "Python " + RuntimePackage.Version + " · 独立环境";
            progress.Report(new InstallProgress("运行环境已就绪", 100));
        }

        private async Task PrepareManagedPythonAsync(IProgress<InstallProgress> progress, CancellationToken cancel)
        {
            EnsureOrdinary(runtime);
            if (Complete(runtime, package.Sha256, "python.exe", "python314.dll", "python314.zip", "python314._pth",
                "_sqlite3.pyd", "sqlite3.dll", "_ssl.pyd", "_ctypes.pyd"))
                return;
            var work = Path.Combine(managed, ".install-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(work);
            try
            {
                string zip = Path.Combine(work, "python.zip");
                progress.Report(new InstallProgress("未检测到本机 Python，正在准备下载", 0));
                await DownloadAsync(zip, progress, cancel).ConfigureAwait(false);
                cancel.ThrowIfCancellationRequested();
                progress.Report(new InstallProgress("正在校验并解压运行环境", 85));
                using (var file = File.OpenRead(zip))
                {
                    if (!String.Equals(Hash(file), package.Sha256, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("运行环境的 SHA-256 校验失败，请重试。");
                    file.Position = 0;
                    InstallZip(file, runtime, package.Sha256, cancel);
                }
            }
            finally
            {
                Directory.Delete(work, true);
            }
        }

        private async Task DownloadAsync(string destination, IProgress<InstallProgress> progress, CancellationToken cancel)
        {
            using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancel))
            using (var http = new HttpClient(httpHandler()))
            {
                timeout.CancelAfter(TimeSpan.FromMinutes(2));
                try
                {
                    using (var response = await http.GetAsync(package.Download, HttpCompletionOption.ResponseHeadersRead,
                        timeout.Token).ConfigureAwait(false))
                    {
                        response.EnsureSuccessStatusCode();
                        long? size = response.Content.Headers.ContentLength;
                        if (size > DownloadLimit) throw new InvalidDataException("运行环境下载大小超出预期。");
                        using (var source = await response.Content.ReadAsStreamAsync().ConfigureAwait(false))
                        using (var target = new FileStream(destination, FileMode.CreateNew, FileAccess.Write,
                            FileShare.None, 65536, true))
                        {
                            var buffer = new byte[65536];
                            long received = 0;
                            int lastPercent = -1;
                            int count;
                            while ((count = await source.ReadAsync(buffer, 0, buffer.Length, timeout.Token)
                                .ConfigureAwait(false)) != 0)
                            {
                                received += count;
                                if (received > DownloadLimit) throw new InvalidDataException("运行环境下载大小超出预期。");
                                await target.WriteAsync(buffer, 0, count, timeout.Token).ConfigureAwait(false);
                                int percent = size > 0 ? (int)Math.Min(80L, 80 * received / size.Value) : -1;
                                if (percent != lastPercent)
                                {
                                    progress.Report(new InstallProgress("正在下载官方 Python 运行环境", percent));
                                    lastPercent = percent;
                                }
                            }
                        }
                    }
                }
                catch (OperationCanceledException)
                {
                    if (cancel.IsCancellationRequested) throw;
                    throw new TimeoutException("下载运行环境超时，请检查网络后重试。");
                }
            }
        }

        private void InstallZip(Stream archive, string destination, string hash, CancellationToken cancel)
        {
            string staging = Path.Combine(managed, ".unpack-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(staging);
            try
            {
                Extract(archive, staging, cancel);
                File.WriteAllText(Path.Combine(staging, Marker), hash, new UTF8Encoding(false));
                cancel.ThrowIfCancellationRequested();
                if (Directory.Exists(destination))
                {
                    EnsureOrdinaryTree(destination);
                    Directory.Delete(destination, true);
                }
                Directory.Move(staging, destination);
            }
            finally
            {
                if (Directory.Exists(staging)) Directory.Delete(staging, true);
            }
        }

        internal void Cleanup(string launcherPath)
        {
            EnsureOrdinary(DataDirectory);
            EnsureOrdinaryTree(managed);
            string executable = Path.GetFullPath(launcherPath);
            if (executable.StartsWith(managed + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new IOException("请将启动器移到运行环境目录之外，再进行清理。");
            string[] names = { "practice.sqlite3", "practice.sqlite3-wal", "practice.sqlite3-shm", "practice.sqlite3-journal" };
            string[] files = names.Select(name => Path.Combine(DataDirectory, name)).ToArray();
            foreach (string file in files) EnsureOrdinary(file);
            if (Directory.Exists(managed)) Directory.Delete(managed, true);
            foreach (string file in files)
                if (File.Exists(file)) File.Delete(file);
            if (Directory.Exists(DataDirectory) && !Directory.EnumerateFileSystemEntries(DataDirectory).Any())
                Directory.Delete(DataDirectory);
        }

        private static bool Complete(string path, string hash, params string[] files)
        {
            string marker = Path.Combine(path, Marker);
            return File.Exists(marker) && File.ReadAllText(marker) == hash
                && files.All(file => File.Exists(Path.Combine(path, file)));
        }

        private static void Extract(Stream source, string destination, CancellationToken cancel)
        {
            string prefix = Path.GetFullPath(destination) + Path.DirectorySeparatorChar;
            using (var archive = new ZipArchive(source, ZipArchiveMode.Read, true))
            {
                long total = 0;
                if (archive.Entries.Count > 2000) throw new InvalidDataException("运行环境包含过多文件。");
                foreach (var entry in archive.Entries)
                {
                    cancel.ThrowIfCancellationRequested();
                    total += entry.Length;
                    if (total > ExpandedLimit || entry.FullName.Contains(":"))
                        throw new InvalidDataException("运行环境的压缩包内容无效。");
                    string path = Path.GetFullPath(Path.Combine(destination, entry.FullName));
                    if (!path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("运行环境的压缩包路径无效。");
                    if (entry.Name.Length == 0)
                    {
                        Directory.CreateDirectory(path);
                    }
                    else
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(path));
                        entry.ExtractToFile(path);
                    }
                }
            }
        }

        private static string Hash(Stream stream)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        private static void EnsureOrdinary(string path)
        {
            try
            {
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("应用目录包含链接，已停止文件操作。");
            }
            catch (FileNotFoundException) { }
            catch (DirectoryNotFoundException) { }
        }

        private static void EnsureOrdinaryTree(string directory)
        {
            EnsureOrdinary(directory);
            if (!Directory.Exists(directory)) return;
            foreach (string entry in Directory.EnumerateFileSystemEntries(directory))
            {
                EnsureOrdinary(entry);
                if (Directory.Exists(entry)) EnsureOrdinaryTree(entry);
            }
        }
    }
}
