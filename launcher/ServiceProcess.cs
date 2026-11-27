using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading.Tasks;

namespace PracticeToolLauncher
{
    internal sealed class ServiceProcess : IDisposable
    {
        internal const int DefaultPort = 8765;
        private const string DirectoryName = "launcher-runtime";
        private const string LogName = "service.log";
        private readonly Process process;
        private readonly string control;
        private readonly string instance;
        internal readonly DateTimeOffset StartedAt;

        private ServiceProcess(Process process, string control, string instance, DateTimeOffset startedAt)
        {
            this.process = process;
            this.control = control;
            this.instance = instance;
            StartedAt = startedAt;
        }

        internal bool IsRunning { get { return !process.HasExited; } }

        private static string ControlDirectory(string dataDirectory)
        {
            return Path.Combine(dataDirectory, DirectoryName, "service");
        }

        internal static ServiceProcess Find(RuntimeInstaller installer)
        {
            string control = ControlDirectory(installer.DataDirectory);
            Process process = null;
            try
            {
                string[] state;
                using (var file = new FileStream(Path.Combine(control, "active"), FileMode.Open,
                    FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var reader = new StreamReader(file, Encoding.ASCII))
                    state = reader.ReadToEnd().Trim().Split(' ');
                Guid instance;
                int pid;
                long seconds;
                if (state.Length != 3 || !Guid.TryParseExact(state[0], "N", out instance)
                    || !Int32.TryParse(state[1], out pid) || !Int64.TryParse(state[2], out seconds))
                    return null;

                var started = DateTimeOffset.FromUnixTimeSeconds(seconds);
                process = Process.GetProcessById(pid);
                if (process.HasExited || process.StartTime.ToUniversalTime() > started.UtcDateTime.AddSeconds(1))
                    return null;
                var result = new ServiceProcess(process, control, instance.ToString("N"), started);
                process = null;
                return result;
            }
            catch (IOException) { return null; }
            catch (ArgumentException) { return null; }
            catch (InvalidOperationException) { return null; }
            catch (Win32Exception) { return null; }
            finally { if (process != null) process.Dispose(); }
        }

        internal static async Task<ServiceProcess> StartAsync(RuntimeInstaller installer, int port)
        {
            string instance = Guid.NewGuid().ToString("N");
            string command = "import sys; sys.path.insert(0, sys.argv.pop(1)); from practice_tool.managed_service import main; sys.exit(main())";
            var start = new ProcessStartInfo
            {
                FileName = installer.PythonPath,
                WorkingDirectory = installer.AppDirectory,
                Arguments = "-I -S -B -u -c \"" + command + "\" \"" + installer.AppDirectory
                    + "\" --data-dir \"" + installer.DataDirectory + "\" --instance " + instance
                    + " --port " + port.ToString(CultureInfo.InvariantCulture),
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using (var child = Process.Start(start))
            {
                if (child == null) throw new InvalidOperationException("无法创建签到服务进程。");
                var timeout = Stopwatch.StartNew();
                while (!child.HasExited && timeout.Elapsed < TimeSpan.FromSeconds(18))
                {
                    var service = Find(installer);
                    if (service != null)
                    {
                        if (service.instance == instance && service.process.Id == child.Id)
                            return service;
                        service.Dispose();
                    }
                    await Task.Delay(200);
                }
                throw new InvalidOperationException(child.HasExited
                    ? "服务启动失败，请查看运行日志并检查端口 " + port + "。"
                    : "服务尚未就绪，请稍候再查看。");
            }
        }

        internal async Task StopAsync()
        {
            if (!IsRunning) return;
            File.WriteAllText(Path.Combine(control, instance + ".stop"), "");
            var timeout = Stopwatch.StartNew();
            while (IsRunning && timeout.Elapsed < TimeSpan.FromSeconds(120))
                await Task.Delay(250);
            if (IsRunning)
                throw new TimeoutException("服务正在等待当前任务结束，可以稍后重新打开启动器查看。");
        }

        internal static string ReadLog(RuntimeInstaller installer)
        {
            try
            {
                using (var file = new FileStream(Path.Combine(ControlDirectory(installer.DataDirectory), LogName),
                    FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    // 加载最后 24 KB，窗口重新打开时也能查看最近记录。
                    long offset = Math.Max(0L, file.Length - 24 * 1024);
                    file.Position = offset;
                    using (var reader = new StreamReader(file, Encoding.UTF8))
                    {
                        if (offset > 0) reader.ReadLine();
                        return reader.ReadToEnd();
                    }
                }
            }
            catch (IOException) { return ""; }
        }

        internal static void LogStartupFailure(Exception error)
        {
            try
            {
                string control = ControlDirectory(RuntimeInstaller.DefaultDataDirectory());
                Directory.CreateDirectory(control);
                File.AppendAllText(Path.Combine(control, LogName), "开机自启失败：" + error.Message + Environment.NewLine);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        public void Dispose() { process.Dispose(); }
    }
}
