using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace PracticeToolLauncher
{
    internal sealed class LauncherForm : Form
    {
        private const int Port = ServiceProcess.DefaultPort;
        private static readonly string Home = "http://127.0.0.1:" + Port + "/";
        private const string Repository = "https://github.com/fqy9242/GxzjsxsPracticeTool";
        private readonly RuntimeInstaller installer;
        private readonly Label state, uptime, runtime, feedback;
        private readonly ProgressBar progress;
        private readonly Button toggle, open, cleanup, copy;
        private readonly CheckBox startup;
        private readonly RichTextBox logs;
        private readonly System.Windows.Forms.Timer timer;
        private readonly CancellationTokenSource lifetime = new CancellationTokenSource();
        private ServiceProcess service;
        private bool prepared, busy, closing, startupAvailable;

        internal LauncherForm(RuntimeInstaller installer)
        {
            this.installer = installer;
            Text = "PracticeTool";
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(645, 460);
            MinimumSize = new Size(620, 410);
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            BackColor = Color.White;
            ForeColor = Color.FromArgb(30, 38, 48);
            Font = new Font("Microsoft YaHei UI", 9F);

            var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(22, 15, 22, 15), RowCount = 9, ColumnCount = 1 };
            foreach (var height in new[] { 43, 27, 27, 27, 36, 27, 30 }) root.RowStyles.Add(new RowStyle(SizeType.Absolute, height));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 7));
            Controls.Add(root);
            var header = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty };
            header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 72));
            header.Controls.Add(TextLabel("PracticeTool", 18, true), 0, 0);
            var github = new LinkLabel { Text = "GitHub", Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleRight };
            github.LinkClicked += (sender, args) => Browse(Repository);
            header.Controls.Add(github, 1, 0);
            root.Controls.Add(header, 0, 0);

            state = TextLabel("服务状态  已停止");
            uptime = TextLabel("已运行  —");
            runtime = TextLabel("运行环境  正在检查");
            root.Controls.Add(state, 0, 1);
            root.Controls.Add(uptime, 0, 2);
            root.Controls.Add(runtime, 0, 3);

            var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, Margin = Padding.Empty, WrapContents = false };
            toggle = ActionButton("启动服务", 90);
            open = ActionButton("打开 Web", 95);
            cleanup = ActionButton("一键清理", 90);
            startup = new CheckBox
            {
                Text = "开机自启", AutoSize = true, AutoCheck = false, Margin = new Padding(9, 6, 0, 0),
                AccessibleDescription = "登录 Windows 后自动启动后台服务",
            };
            progress = new ProgressBar { Width = 132, Height = 8, Margin = new Padding(9, 12, 0, 0), Visible = false };
            actions.Controls.AddRange(new Control[] { toggle, open, cleanup, startup, progress });
            root.Controls.Add(actions, 0, 4);

            feedback = TextLabel("关闭窗口后，后台服务会继续运行。");
            root.Controls.Add(feedback, 0, 5);
            var toolbar = new FlowLayoutPanel { Dock = DockStyle.Fill, Margin = Padding.Empty, WrapContents = false };
            toolbar.Controls.Add(new Label { Text = "服务日志", AutoSize = true, Margin = new Padding(0, 7, 9, 0), Font = new Font(Font, FontStyle.Bold) });
            copy = ActionButton("复制", 58);
            toolbar.Controls.Add(copy);
            root.Controls.Add(toolbar, 0, 6);
            logs = new RichTextBox
            {
                Dock = DockStyle.Fill, Margin = Padding.Empty, ReadOnly = true, DetectUrls = false,
                BackColor = Color.FromArgb(248, 249, 250), AccessibleName = "服务日志",
            };
            root.Controls.Add(logs, 0, 7);

            timer = new System.Windows.Forms.Timer { Interval = 1000 };
            timer.Tick += (sender, args) => RefreshService();
            toggle.Click += async (sender, args) => await RunAsync(ToggleAsync);
            open.Click += (sender, args) => Browse(Home);
            cleanup.Click += async (sender, args) => await ConfirmCleanupAsync();
            startup.Click += (sender, args) => ToggleStartup();
            copy.Click += (sender, args) =>
            {
                try { if (logs.TextLength != 0) Clipboard.SetText(logs.Text); }
                catch (System.Runtime.InteropServices.ExternalException) { feedback.Text = "剪贴板暂时不可用。"; }
            };
            Shown += async (sender, args) =>
            {
                ReadStartupSetting();
                RefreshService();
                timer.Start();
                if (service == null) await RunAsync(PrepareAsync);
                else runtime.Text = "运行环境  服务已就绪";
            };
            FormClosing += OnClosing;
            FormClosed += (sender, args) =>
            {
                timer.Dispose();
                lifetime.Dispose();
                if (service != null) service.Dispose();
            };
            UpdateButtons();
        }

        private static Label TextLabel(string value, float size = 9, bool bold = false)
        {
            return new Label
            {
                Text = value, Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true,
                Font = new Font("Microsoft YaHei UI", size, bold ? FontStyle.Bold : FontStyle.Regular), Margin = Padding.Empty,
            };
        }

        private static Button ActionButton(string title, int width)
        {
            return new Button { Text = title, Width = width, Height = 27, Margin = new Padding(0, 1, 7, 0) };
        }

        private void RefreshService()
        {
            if (closing) return;
            if (service == null && !busy) service = ServiceProcess.Find(installer);
            if (service != null && !service.IsRunning && !busy)
            {
                service.Dispose();
                service = null;
            }

            if (!busy) state.Text = service == null ? "服务状态  已停止" : "服务状态  运行中";
            if (service == null)
                uptime.Text = "已运行  —";
            else
            {
                TimeSpan duration = DateTimeOffset.UtcNow - service.StartedAt;
                if (duration < TimeSpan.Zero) duration = TimeSpan.Zero;
                uptime.Text = "已运行  " + (duration.Days > 0 ? duration.Days + " 天 " : "")
                    + duration.ToString(@"hh\:mm\:ss");
            }
            string text = ServiceProcess.ReadLog(installer);
            if (logs.Text != text.Replace("\r\n", "\n"))
            {
                logs.Text = text;
                logs.SelectionStart = logs.TextLength;
                logs.ScrollToCaret();
            }
            UpdateButtons();
        }

        private void UpdateButtons()
        {
            toggle.Text = service != null ? "停止服务" : "启动服务";
            toggle.Enabled = !closing && !busy;
            open.Enabled = !closing && !busy && service != null;
            cleanup.Enabled = !closing && !busy && installer.HasFiles;
            startup.Enabled = !closing && !busy && startupAvailable;
            copy.Enabled = logs.TextLength != 0;
        }

        private void ReadStartupSetting()
        {
            try
            {
                startup.Checked = StartupRegistration.IsEnabled();
                startupAvailable = true;
            }
            catch (Exception error) { feedback.Text = "无法读取开机自启设置：" + error.Message; }
        }

        private void ToggleStartup()
        {
            try
            {
                bool enabled = !startup.Checked;
                StartupRegistration.SetEnabled(enabled, Application.ExecutablePath);
                startup.Checked = enabled;
                feedback.Text = enabled ? "登录 Windows 时将自动启动后台服务。" : "已关闭开机自启。";
            }
            catch (Exception error) { feedback.Text = "无法更新开机自启设置：" + error.Message; }
        }

        private async Task RunAsync(Func<Task> action)
        {
            if (busy || closing) return;
            busy = true;
            UpdateButtons();
            try { await action(); }
            catch (OperationCanceledException) { if (!closing) feedback.Text = "操作已取消。"; }
            catch (Exception error) { feedback.Text = error.Message; }
            finally
            {
                busy = false;
                RefreshService();
                UpdateButtons();
                if (closing) BeginInvoke(new Action(Close));
            }
        }

        private async Task PrepareAsync()
        {
            runtime.Text = "运行环境  正在检查";
            progress.Style = ProgressBarStyle.Marquee;
            progress.Visible = true;
            try
            {
                var updates = new Progress<InstallProgress>(update =>
                {
                    if (prepared || closing) return;
                    runtime.Text = "运行环境  " + update.Message;
                    progress.Style = update.Percent < 0 ? ProgressBarStyle.Marquee : ProgressBarStyle.Blocks;
                    if (update.Percent >= 0) progress.Value = update.Percent;
                });
                await Task.Run(() => installer.PrepareAsync(updates, lifetime.Token));
                prepared = true;
                runtime.Text = "运行环境  " + installer.PythonDescription;
            }
            finally { progress.Visible = false; }
        }

        private async Task ToggleAsync()
        {
            if (service != null)
            {
                await StopAsync();
                return;
            }
            if (!prepared || !File.Exists(installer.PythonPath))
            {
                prepared = false;
                await PrepareAsync();
            }
            if (closing) return;
            state.Text = "服务状态  正在启动";
            service = await ServiceProcess.StartAsync(installer, Port);
            feedback.Text = "关闭窗口后，后台服务会继续运行。";
        }

        private async Task StopAsync()
        {
            if (service == null) return;
            state.Text = "服务状态  正在停止";
            await service.StopAsync();
            service.Dispose();
            service = null;
            feedback.Text = "后台服务已停止。";
        }

        private async Task ConfirmCleanupAsync()
        {
            if (busy || closing) return;
            if (MessageBox.Show(this, "清理会关闭开机自启，并删除运行环境和本机保存的账号、计划及记录。确定继续吗？",
                "清理 PracticeTool", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2)
                != DialogResult.Yes) return;
            await RunAsync(async () =>
            {
                await StopAsync();
                prepared = false;
                await Task.Run(new Action(Cleanup));
                ReadStartupSetting();
                runtime.Text = "运行环境  已清理";
                feedback.Text = "本机应用数据已清理。";
            });
        }

        private void Cleanup()
        {
            var guard = new TcpListener(IPAddress.Loopback, Port) { ExclusiveAddressUse = true };
            try
            {
                guard.Start();
                StartupRegistration.SetEnabled(false, Application.ExecutablePath);
                installer.Cleanup(Application.ExecutablePath);
            }
            catch (SocketException) { throw new IOException("端口 8765 仍有服务运行，请先关闭该服务。"); }
            finally { guard.Stop(); }
        }

        private void Browse(string address)
        {
            try { Process.Start(new ProcessStartInfo(address) { UseShellExecute = true }); }
            catch (Exception) { feedback.Text = "无法打开浏览器。可手动访问 " + address; }
        }

        private void OnClosing(object sender, FormClosingEventArgs args)
        {
            if (!busy) return;
            args.Cancel = true;
            if (!closing)
            {
                closing = true;
                lifetime.Cancel();
                UpdateButtons();
            }
        }
    }
}
