using System;
using System.IO;
using Microsoft.Win32;

namespace PracticeToolLauncher
{
    internal static class StartupRegistration
    {
        internal const string Argument = "--startup";
        private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string ValueName = "PracticeTool";

        internal static bool IsEnabled()
        {
            using (var key = Registry.CurrentUser.OpenSubKey(RunKey))
                return key != null && key.GetValue(ValueName) != null;
        }

        internal static void SetEnabled(bool enabled, string executable)
        {
            if (!enabled)
            {
                using (var key = Registry.CurrentUser.OpenSubKey(RunKey, writable: true))
                    if (key != null) key.DeleteValue(ValueName, throwOnMissingValue: false);
                return;
            }

            string command = "\"" + Path.GetFullPath(executable) + "\" " + Argument;
            if (command.Length > 260)
                throw new InvalidOperationException("启动器路径过长，请移到较短的固定路径后再启用开机自启。");
            using (var key = Registry.CurrentUser.CreateSubKey(RunKey))
                key.SetValue(ValueName, command, RegistryValueKind.String);
        }
    }
}
