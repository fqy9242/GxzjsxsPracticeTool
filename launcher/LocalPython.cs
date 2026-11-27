using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Security;
using System.Threading;
using Microsoft.Win32;

namespace PracticeToolLauncher
{
    internal sealed class LocalPython
    {
        private const string ProbePrefix = "PRACTICETOOL_PYTHON ";
        private const string Probe = "import sys, ssl, sqlite3, ctypes; "
            + "print('PRACTICETOOL_PYTHON ' + '.'.join(map(str, sys.version_info[:3])))";

        internal readonly string Path;
        internal readonly Version Version;

        private LocalPython(string path, Version version)
        {
            Path = path;
            Version = version;
        }

        internal static LocalPython Find(CancellationToken cancel)
        {
            var checkedPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string path in Candidates())
            {
                cancel.ThrowIfCancellationRequested();
                if (!File.Exists(path) || !checkedPaths.Add(path)
                    || path.IndexOf(@"\Microsoft\WindowsApps\", StringComparison.OrdinalIgnoreCase) >= 0)
                    continue;
                var version = Check(path, cancel);
                if (version != null) return new LocalPython(path, version);
            }
            return null;
        }

        private static IEnumerable<string> Candidates()
        {
            foreach (var folder in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
            {
                string directory = Environment.ExpandEnvironmentVariables(folder.Trim().Trim('"'));
                if (!IsLocalAbsolute(directory)) continue;
                yield return System.IO.Path.Combine(directory, "python.exe");
                yield return System.IO.Path.Combine(directory, "python3.exe");
            }

            foreach (var hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
            {
                foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
                {
                    foreach (var executable in Registered(hive, view)) yield return executable;
                }
            }
        }

        private static List<string> Registered(RegistryHive hive, RegistryView view)
        {
            var paths = new List<string>();
            try
            {
                using (var root = RegistryKey.OpenBaseKey(hive, view))
                using (var python = root.OpenSubKey(@"Software\Python"))
                {
                    if (python == null) return paths;
                    foreach (var vendor in python.GetSubKeyNames())
                    using (var company = python.OpenSubKey(vendor))
                    {
                        if (company == null) continue;
                        foreach (var tag in company.GetSubKeyNames())
                        using (var install = company.OpenSubKey(tag + @"\InstallPath"))
                        {
                            if (install == null || !String.IsNullOrWhiteSpace(install.GetValue("ExecutableArguments") as string))
                                continue;
                            string executable = install.GetValue("ExecutablePath") as string;
                            string directory = install.GetValue("") as string;
                            if (executable == null && vendor == "PythonCore" && IsLocalAbsolute(directory))
                                executable = System.IO.Path.Combine(directory, "python.exe");
                            if (IsLocalAbsolute(executable))
                                paths.Add(executable);
                        }
                    }
                }
            }
            catch (SecurityException) { }
            catch (UnauthorizedAccessException) { }
            catch (IOException) { }
            return paths;
        }

        private static bool IsLocalAbsolute(string path)
        {
            return !String.IsNullOrWhiteSpace(path) && path.Length >= 3 && Char.IsLetter(path[0])
                && path[1] == ':' && (path[2] == '\\' || path[2] == '/')
                && path.IndexOfAny(System.IO.Path.GetInvalidPathChars()) < 0;
        }

        private static Version Check(string path, CancellationToken cancel)
        {
            using (var python = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = path,
                    Arguments = "-I -S -B -c \"" + Probe + "\"",
                    WorkingDirectory = Environment.SystemDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                }
            })
            {
                Version result = null;
                python.OutputDataReceived += (sender, args) =>
                {
                    string line = args.Data;
                    Version found;
                    if (line != null && line.StartsWith(ProbePrefix, StringComparison.Ordinal)
                        && Version.TryParse(line.Substring(ProbePrefix.Length), out found))
                        result = found;
                };
                try
                {
                    if (!python.Start()) return null;
                    python.BeginOutputReadLine();
                    try
                    {
                        var deadline = Stopwatch.StartNew();
                        while (!python.WaitForExit(125))
                        {
                            cancel.ThrowIfCancellationRequested();
                            if (deadline.ElapsedMilliseconds > 4000) return null;
                        }
                        python.WaitForExit();
                        return python.ExitCode == 0 && result != null && result.Major == 3 && result.Minor >= 9
                            ? result : null;
                    }
                    finally
                    {
                        if (!python.HasExited)
                        {
                            try { python.Kill(); }
                            catch (InvalidOperationException) { }
                            catch (Win32Exception) { }
                        }
                    }
                }
                catch (Win32Exception) { return null; }
                catch (UnauthorizedAccessException) { return null; }
            }
        }
    }
}
