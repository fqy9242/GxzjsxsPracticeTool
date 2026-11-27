using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.ExceptionServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace PracticeToolLauncher
{
    internal sealed class LauncherForm : Form
    {
        private const int Port = 8765;
        private const string Home = "http://127.0.0.1:8765/";
        private static readonly Color Canvas = Color.FromArgb(244, 247, 252);
        private static readonly Color Ink = Color.FromArgb(22, 34, 56);
        private static readonly Color Muted = Color.FromArgb(86, 103, 129);
        private static readonly Color Accent = Color.FromArgb(36, 87, 213);
        private static readonly Color Line = Color.FromArgb(218, 226, 237);
        private static readonly Color White = Color.White;
        private static readonly Color Success = Color.FromArgb(21, 114, 87);
        private static readonly Color Danger = Color.FromArgb(169, 58, 54);
        private readonly RuntimeInstaller installer;
        private readonly Label serviceState, runtimeState, runtimeDetail, serviceDescription;
        private readonly ProgressBar progressBar;
        private readonly Button serviceButton, webButton, cleanupButton, copyButton;
        private readonly RichTextBox logs;
        private readonly CancellationTokenSource lifetime = new CancellationTokenSource();
        private ServiceProcess service;
        private bool runtimeReady, busy, closing;
        private int downloadPercent;
        private string lastProgress;

        internal LauncherForm(RuntimeInstaller installer)
        {
            this.installer = installer;
            Text = "PracticeTool · 服务控制台";
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(858, 625);
            MinimumSize = new Size(745, 555);
            BackColor = Canvas;
            ForeColor = Ink;
            Font = new Font("Microsoft YaHei UI", 9F);
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);

            var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(27, 23, 27, 22), RowCount = 4, ColumnCount = 1 };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 78));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 172));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 22));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            Controls.Add(root);
            root.Controls.Add(Header(), 0, 0);

            var cards = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty };
            cards.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 57));
            cards.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 43));
            root.Controls.Add(cards, 0, 1);

            var controls = new Surface { Dock = DockStyle.Fill, Margin = new Padding(0, 0, 8, 0) };
            cards.Controls.Add(controls, 0, 0);
            var serviceGrid = CardGrid();
            controls.Controls.Add(serviceGrid);
            serviceState = Label("", 17, Ink, true);
            serviceDescription = Label("服务运行期间执行自动签到计划。", 9, Muted);
            var serviceButtons = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Margin = Padding.Empty };
            serviceButton = Button("启动服务", true, 94);
            webButton = Button("打开 Web 首页", false, 125);
            serviceButtons.Controls.Add(serviceButton);
            serviceButtons.Controls.Add(webButton);
            serviceGrid.Controls.Add(Label("签到服务", 10, Muted, true), 0, 0);
            serviceGrid.Controls.Add(serviceState, 0, 1);
            serviceGrid.Controls.Add(serviceDescription, 0, 2);
            serviceGrid.Controls.Add(serviceButtons, 0, 3);

            var runtime = new Surface { Dock = DockStyle.Fill, Margin = new Padding(8, 0, 0, 0) };
            cards.Controls.Add(runtime, 1, 0);
            var runtimeGrid = CardGrid();
            runtime.Controls.Add(runtimeGrid);
            runtimeState = Label("正在检查", 17, Ink, true);
            runtimeDetail = Label("正在检查本机运行环境。", 9, Muted);
            progressBar = new ProgressBar { Height = 8, Margin = new Padding(0, 2, 0, 0), Dock = DockStyle.Top };
            cleanupButton = Button("一键清理", false, 94);
            cleanupButton.ForeColor = Danger;
            var runtimeActions = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Margin = Padding.Empty };
            runtimeActions.Controls.Add(cleanupButton);
            runtimeGrid.Controls.Add(Label("运行环境", 10, Muted, true), 0, 0);
            runtimeGrid.Controls.Add(runtimeState, 0, 1);
            runtimeGrid.Controls.Add(runtimeDetail, 0, 2);
            runtimeGrid.Controls.Add(runtimeActions, 0, 3);
            runtimeGrid.Controls.Add(progressBar, 0, 4);

            var logSurface = new Surface { Dock = DockStyle.Fill, Margin = Padding.Empty };
            root.Controls.Add(logSurface, 0, 3);
            var logGrid = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1, Padding = new Padding(19, 14, 19, 18) };
            logGrid.RowStyles.Add(new RowStyle(SizeType.Absolute, 44));
            logGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            logSurface.Controls.Add(logGrid);
            var toolbar = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty };
            toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 95));
            toolbar.Controls.Add(Label("运行日志   ·   北京时间", 11, Ink, true), 0, 0);
            copyButton = Button("复制日志", false, 94);
            toolbar.Controls.Add(copyButton, 1, 0);
            logGrid.Controls.Add(toolbar, 0, 0);
            logs = new RichTextBox
            {
                Dock = DockStyle.Fill, Margin = Padding.Empty, ReadOnly = true, BorderStyle = BorderStyle.FixedSingle,
                BackColor = Color.FromArgb(248, 250, 253), ForeColor = Ink, DetectUrls = false,
                Font = new Font("Microsoft YaHei UI", 9F), AccessibleName = "运行日志",
            };
            logGrid.Controls.Add(logs, 0, 1);

            serviceButton.Click += async (sender, args) => await OperateAsync(ToggleServiceAsync);
            webButton.Click += (sender, args) => OpenHome();
            cleanupButton.Click += async (sender, args) => await ConfirmCleanupAsync();
            copyButton.Click += (sender, args) => CopyLogs();
            Shown += async (sender, args) => await OperateAsync(PrepareAsync);
            FormClosing += OnClosing;
            UpdateControls();
        }

        private static Control Header()
        {
            var header = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 2, Margin = Padding.Empty };
            header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 60));
            header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 180));
            header.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
            header.RowStyles.Add(new RowStyle(SizeType.Absolute, 27));
            var brand = new Brand { Size = new Size(46, 46), Margin = new Padding(0, 5, 0, 0) };
            header.Controls.Add(brand, 0, 0);
            header.SetRowSpan(brand, 2);
            header.Controls.Add(Label("PracticeTool", 20, Ink, true), 1, 0);
            header.Controls.Add(Label("实习签到服务控制台", 9, Muted), 1, 1);
            var local = Label("本机管理  /  127.0.0.1:8765", 9, Muted);
            local.TextAlign = ContentAlignment.MiddleRight;
            header.Controls.Add(local, 2, 0);
            header.SetRowSpan(local, 2);
            return header;
        }

        private static TableLayoutPanel CardGrid()
        {
            var grid = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 5, Padding = new Padding(19, 11, 16, 4) };
            foreach (var height in new[] { 22, 36, 28, 41, 15 }) grid.RowStyles.Add(new RowStyle(SizeType.Absolute, height));
            return grid;
        }

        private static Label Label(string text, float size, Color color, bool bold = false)
        {
            return new Label
            {
                Text = text, ForeColor = color, Font = new Font("Microsoft YaHei UI", size, bold ? FontStyle.Bold : FontStyle.Regular),
                Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft, Margin = Padding.Empty, AutoEllipsis = true,
                BackColor = Color.Transparent,
            };
        }

        private static Button Button(string text, bool primary, int width)
        {
            var button = new Button
            {
                Text = text, Width = width, Height = 34, Margin = new Padding(0, 2, 9, 0), FlatStyle = FlatStyle.Flat,
                Cursor = Cursors.Hand, BackColor = primary ? Accent : White, ForeColor = primary ? White : Ink,
                Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold), UseVisualStyleBackColor = false,
            };
            button.FlatAppearance.BorderColor = primary ? Accent : Line;
            button.FlatAppearance.MouseOverBackColor = primary ? Color.FromArgb(28, 72, 190) : Canvas;
            return button;
        }

        private async Task OperateAsync(Func<Task> action)
        {
            if (busy || closing) return;
            busy = true;
            UpdateControls();
            try { await action(); }
            catch (Exception error)
            {
                if (!(closing && error is OperationCanceledException))
                {
                    Log(error.Message, true);
                    if (!runtimeReady)
                    {
                        runtimeState.Text = "安装未完成";
                        runtimeState.ForeColor = Danger;
                        runtimeDetail.Text = "点击「准备环境」重试。";
                        progressBar.Style = ProgressBarStyle.Blocks;
                        progressBar.Value = 0;
                    }
                    else if (service == null)
                    {
                        serviceState.Text = "已停止";
                        serviceState.ForeColor = Muted;
                        serviceDescription.Text = "操作未完成，详情见运行日志。";
                    }
                }
            }
            finally
            {
                busy = false;
                UpdateControls();
            }
            if (closing) await FinishClosingAsync();
        }

        private async Task PrepareAsync()
        {
            runtimeState.Text = "正在准备";
            runtimeState.ForeColor = Accent;
            runtimeDetail.Text = "正在检查已下载的文件。";
            lastProgress = "";
            downloadPercent = 0;
            progressBar.Style = ProgressBarStyle.Marquee;
            var progress = new Progress<InstallProgress>(ReportProgress);
            await Task.Run(() => installer.PrepareAsync(progress, lifetime.Token));
            runtimeReady = true;
            SetRuntimeReady();
            Log("运行环境已就绪。" + installer.PythonDescription);
        }

        private void ReportProgress(InstallProgress update)
        {
            if (closing || runtimeReady) return;
            runtimeDetail.Text = update.Percent < 0 ? update.Message : update.Message + " · " + update.Percent + "%";
            progressBar.Style = update.Percent < 0 ? ProgressBarStyle.Marquee : ProgressBarStyle.Blocks;
            if (update.Percent >= 0) progressBar.Value = update.Percent;
            if (lastProgress != update.Message || update.Percent - downloadPercent >= 20)
            {
                Log(runtimeDetail.Text);
                lastProgress = update.Message;
                downloadPercent = Math.Max(0, update.Percent);
            }
        }

        private void SetRuntimeReady()
        {
            runtimeState.Text = "已就绪";
            runtimeState.ForeColor = Success;
            runtimeDetail.Text = installer.PythonDescription;
            progressBar.Style = ProgressBarStyle.Blocks;
            progressBar.Value = 100;
        }

        private async Task ToggleServiceAsync()
        {
            if (service != null)
            {
                await StopServiceAsync();
                return;
            }
            if (!runtimeReady || !File.Exists(installer.PythonPath))
            {
                runtimeReady = false;
                await PrepareAsync();
            }
            if (closing) return;
            serviceState.Text = "正在启动";
            serviceState.ForeColor = Accent;
            serviceDescription.Text = "正在加载签到服务。";
            Log("正在启动签到服务。");
            var running = new ServiceProcess(installer, Port, LogFromService);
            service = running;
            ExceptionDispatchInfo failure = null;
            try { await running.StartAsync(); }
            catch (Exception error) { failure = ExceptionDispatchInfo.Capture(error); }
            if (failure != null)
            {
                await StopServiceAsync();
                failure.Throw();
            }
            if (closing) return;
            serviceState.Text = "运行中";
            serviceState.ForeColor = Success;
            serviceDescription.Text = "自动签到计划正在运行。";
            Log("服务已启动，可以打开 Web 首页。");
            ObserveExit(running);
        }

        private async void ObserveExit(ServiceProcess running)
        {
            int result = await running.Exited;
            if (service != running || closing) return;
            if (busy)
            {
                await Task.Delay(150);
                if (service != running || busy || closing) return;
            }
            service = null;
            running.Dispose();
            serviceState.Text = "已停止";
            serviceState.ForeColor = Muted;
            serviceDescription.Text = "服务进程已退出，详情见运行日志。";
            Log("签到服务已退出（" + result + "）。", result != 0);
            UpdateControls();
        }

        private async Task StopServiceAsync()
        {
            var running = service;
            if (running == null) return;
            serviceState.Text = "正在停止";
            serviceState.ForeColor = Accent;
            serviceDescription.Text = "正在等待当前任务结束。";
            Log("正在停止服务。");
            bool graceful = await running.StopAsync();
            service = null;
            running.Dispose();
            serviceState.Text = "已停止";
            serviceState.ForeColor = Muted;
            serviceDescription.Text = "服务运行期间执行自动签到计划。";
            Log(graceful ? "服务已停止。" : "等候超时，服务已关闭。", !graceful);
        }

        private async Task ConfirmCleanupAsync()
        {
            if (busy || closing) return;
            string text = "清理将停止签到服务，并移除下载的运行环境和本机保存的账号、会话、签到计划及执行记录。\n\n"
                + "清理完成后，启动器仍可保留。确定继续吗？";
            if (MessageBox.Show(this, text, "一键清理 PracticeTool", MessageBoxButtons.YesNo, MessageBoxIcon.Warning,
                MessageBoxDefaultButton.Button2) != DialogResult.Yes) return;
            await OperateAsync(async () =>
            {
                await StopServiceAsync();
                Log("正在清理本机运行环境和应用数据。");
                try { await Task.Run(new Action(Cleanup)); }
                catch
                {
                    runtimeReady = false;
                    runtimeState.Text = "清理未完成";
                    runtimeState.ForeColor = Danger;
                    runtimeDetail.Text = "请查看日志后重试。";
                    return;
                }
                runtimeReady = false;
                runtimeState.Text = "已清理";
                runtimeState.ForeColor = Muted;
                runtimeDetail.Text = "下次打开时会重新准备运行环境。";
                progressBar.Value = 0;
                logs.Clear();
                Log("清理完成，已移除运行环境和本机应用数据。");
            });
        }

        private void Cleanup()
        {
            var guard = new TcpListener(IPAddress.Loopback, Port) { ExclusiveAddressUse = true };
            try
            {
                guard.Start();
                installer.Cleanup(Application.ExecutablePath);
            }
            catch (SocketException)
            {
                var message = "端口 8765 仍有服务运行，请先关闭该服务，再清理。";
                LogFromService(message, true);
                throw new IOException(message);
            }
            catch (Exception error)
            {
                LogFromService(error.Message, true);
                throw;
            }
            finally { guard.Stop(); }
        }

        private void UpdateControls()
        {
            if (serviceState.Text.Length == 0)
            {
                serviceState.Text = "已停止";
                serviceState.ForeColor = Muted;
            }
            serviceButton.Text = service != null ? "停止服务" : runtimeReady ? "启动服务" : "准备环境";
            serviceButton.Enabled = !busy && !closing;
            webButton.Enabled = !busy && !closing && service != null;
            cleanupButton.Enabled = !busy && !closing && installer.HasFiles;
            copyButton.Enabled = logs.TextLength > 0;
        }

        private void OpenHome()
        {
            try { Process.Start(new ProcessStartInfo(Home) { UseShellExecute = true }); }
            catch (Exception error) { Log("无法打开浏览器。请访问 " + Home + " " + error.Message, true); }
        }

        private void CopyLogs()
        {
            try { if (logs.TextLength > 0) Clipboard.SetText(logs.Text); }
            catch (System.Runtime.InteropServices.ExternalException) { Log("剪贴板暂时不可用。", true); }
        }

        private void LogFromService(string message, bool warning)
        {
            if (IsDisposed || !IsHandleCreated) return;
            try { BeginInvoke(new Action(() => Log(message, warning))); }
            catch (InvalidOperationException) { }
        }

        private void Log(string message, bool warning = false)
        {
            string time = DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(8)).ToString("HH:mm:ss");
            logs.SelectionStart = logs.TextLength;
            logs.SelectionColor = Muted;
            logs.AppendText("[" + time + "]  ");
            logs.SelectionColor = warning ? Danger : Ink;
            string compact = message.Trim().Replace('\r', ' ').Replace('\n', ' ');
            logs.AppendText((compact.Length > 1000 ? compact.Substring(0, 1000) : compact) + Environment.NewLine);
            if (logs.Lines.Length > 351)
            {
                int index = logs.GetFirstCharIndexFromLine(logs.Lines.Length - 301);
                logs.Select(0, index);
                logs.SelectedText = "";
            }
            logs.SelectionStart = logs.TextLength;
            logs.ScrollToCaret();
            copyButton.Enabled = true;
        }

        private async void OnClosing(object sender, FormClosingEventArgs args)
        {
            if (!busy && service == null)
            {
                lifetime.Dispose();
                return;
            }
            args.Cancel = true;
            if (!closing)
            {
                closing = true;
                lifetime.Cancel();
                UpdateControls();
                if (!busy) await FinishClosingAsync();
            }
        }

        private async Task FinishClosingAsync()
        {
            try { await StopServiceAsync(); }
            catch (Exception error) { Log(error.Message, true); closing = false; UpdateControls(); return; }
            BeginInvoke(new Action(Close));
        }

        private sealed class Surface : Panel
        {
            internal Surface() { DoubleBuffered = true; BackColor = White; }
            protected override void OnPaint(PaintEventArgs args)
            {
                base.OnPaint(args);
                args.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                using (var pen = new Pen(Line))
                    args.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
            }
        }

        private sealed class Brand : Control
        {
            protected override void OnPaint(PaintEventArgs args)
            {
                base.OnPaint(args);
                args.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                using (var area = new GraphicsPath())
                using (var fill = new SolidBrush(Accent))
                {
                    const int diameter = 17;
                    area.AddArc(0, 0, diameter, diameter, 180, 90);
                    area.AddArc(Width - diameter, 0, diameter, diameter, 270, 90);
                    area.AddArc(Width - diameter, Height - diameter, diameter, diameter, 0, 90);
                    area.AddArc(0, Height - diameter, diameter, diameter, 90, 90);
                    area.CloseFigure();
                    args.Graphics.FillPath(fill, area);
                }
                using (var font = new Font("Segoe UI", 23F, FontStyle.Bold))
                    TextRenderer.DrawText(args.Graphics, "P", font, ClientRectangle, White,
                        TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
            }
        }
    }
}
