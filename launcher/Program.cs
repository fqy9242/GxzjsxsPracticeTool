using System;
using System.Net;
using System.Reflection;
using System.Security.Principal;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("PracticeTool")]
[assembly: AssemblyProduct("PracticeTool")]
[assembly: AssemblyDescription("实习签到服务控制台")]
[assembly: AssemblyVersion("2.0.0.0")]
[assembly: AssemblyFileVersion("2.0.0.0")]

namespace PracticeToolLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            ServicePointManager.SecurityProtocol = SecurityProtocolType.SystemDefault;
            string user;
            using (var identity = WindowsIdentity.GetCurrent()) user = identity.User.Value;
            bool first;
            using (var instance = new Mutex(true, @"Global\PracticeTool.Launcher." + user, out first))
            {
                if (!first)
                {
                    MessageBox.Show("PracticeTool 启动器已经打开。", "PracticeTool", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return;
                }

                try { Application.Run(new LauncherForm(RuntimeInstaller.Create())); }
                catch (Exception error)
                {
                    MessageBox.Show(error.Message, "PracticeTool 无法启动", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }
    }
}
