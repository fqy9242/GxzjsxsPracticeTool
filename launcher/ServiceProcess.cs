using System;
using System.Diagnostics;
using System.Text;
using System.Threading.Tasks;

namespace PracticeToolLauncher
{
    internal sealed class ServiceProcess : IDisposable
    {
        private readonly Process process;
        private readonly TaskCompletionSource<bool> ready =
            new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        private readonly TaskCompletionSource<int> exit =
            new TaskCompletionSource<int>(TaskCreationOptions.RunContinuationsAsynchronously);
        private bool started;

        internal ServiceProcess(RuntimeInstaller installer, int port, Action<string, bool> log)
        {
            string command = "import sys; sys.path.insert(0, sys.argv.pop(1)); from practice_tool.managed_service import main; sys.exit(main())";
            process = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = installer.PythonPath,
                    WorkingDirectory = installer.AppDirectory,
                    Arguments = "-B -u -c \"" + command + "\" \"" + installer.AppDirectory
                        + "\" --data-dir \"" + installer.DataDirectory + "\" --port " + port,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardInput = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8,
                }
            };
            process.OutputDataReceived += (sender, eventArgs) =>
            {
                string line = eventArgs.Data;
                if (line == "PRACTICETOOL_READY " + port)
                    ready.TrySetResult(true);
                else if (!String.IsNullOrWhiteSpace(line))
                    log(line, false);
            };
            process.ErrorDataReceived += (sender, eventArgs) =>
            {
                if (!String.IsNullOrWhiteSpace(eventArgs.Data))
                    log(eventArgs.Data, true);
            };
            process.EnableRaisingEvents = true;
            process.Exited += (sender, eventArgs) => exit.TrySetResult(process.ExitCode);
        }

        internal Task<int> Exited { get { return exit.Task; } }

        internal async Task StartAsync()
        {
            started = process.Start();
            if (!started) throw new InvalidOperationException("无法创建签到服务进程。");
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            Task first = await Task.WhenAny(ready.Task, exit.Task, Task.Delay(TimeSpan.FromSeconds(18)));
            if (exit.Task.IsCompleted)
                throw new InvalidOperationException("签到服务启动失败，请查看日志。");
            if (first != ready.Task)
                throw new TimeoutException("签到服务启动超时，请查看日志。");
        }

        internal async Task<bool> StopAsync()
        {
            if (!started || exit.Task.IsCompleted) return true;
            try { process.StandardInput.Close(); }
            catch (System.IO.IOException) { }
            Task ended = await Task.WhenAny(exit.Task, Task.Delay(TimeSpan.FromSeconds(110)));
            if (ended == exit.Task) return true;

            try { if (!process.HasExited) process.Kill(); }
            catch (InvalidOperationException) { }
            await exit.Task;
            return false;
        }

        public void Dispose()
        {
            process.Dispose();
        }
    }
}
