using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;

namespace FuckRubbishClass.Services;

public interface IBackendSetupService
{
    Task<bool> ArePythonDependenciesInstalledAsync(CancellationToken cancellationToken = default);
    Task InstallPythonDependenciesAsync(Action<string>? reportProgress = null, CancellationToken cancellationToken = default);
}

public sealed class BackendSetupService : IBackendSetupService
{
    private const string PythonProbe = "import sys; print(sys.executable); print(sys.version_info[0] * 10000 + sys.version_info[1] * 100 + sys.version_info[2])";
    private const string DependencyProbe = "import importlib.metadata,importlib.util,re,sys; names=('PIL','Crypto','requests','tiktoken','openai','playwright'); missing=[name for name in names if importlib.util.find_spec(name) is None]; match=re.match(r'^(\\d+)\\.(\\d+)',importlib.metadata.version('playwright')); sys.exit(0 if sys.version_info >= (3,10) and not missing and match and (int(match.group(1)),int(match.group(2))) >= (1,56) and int(match.group(1)) < 2 else 1)";

    public async Task<bool> ArePythonDependenciesInstalledAsync(CancellationToken cancellationToken = default)
    {
        var backendRoot = PythonBridgeService.FindBackendRoot();
        var python = await FindRuntimePythonAsync(backendRoot, cancellationToken);
        return python != null && await ProbeDependenciesAsync(python, cancellationToken);
    }

    public async Task InstallPythonDependenciesAsync(Action<string>? reportProgress = null,
        CancellationToken cancellationToken = default)
    {
        var backendRoot = PythonBridgeService.FindBackendRoot();
        var appRoot = Directory.GetParent(backendRoot)?.FullName
            ?? throw new DirectoryNotFoundException("无法定位应用目录。");
        var scriptPath = Path.Combine(appRoot, "scripts", "Setup-Backend.ps1");
        if (!File.Exists(scriptPath))
            throw new FileNotFoundException("未找到后端安装脚本，请重新解压完整发布包。", scriptPath);

        var python = await FindPythonAsync(backendRoot, cancellationToken);
        reportProgress?.Invoke("正在安装 Python 后端依赖…");

        var startInfo = new ProcessStartInfo("powershell.exe")
        {
            WorkingDirectory = appRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        foreach (var argument in new[]
        {
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", scriptPath, "-Python", python, "-SkipKetangpai"
        }) startInfo.ArgumentList.Add(argument);

        using var process = new Process { StartInfo = startInfo };
        if (!process.Start()) throw new InvalidOperationException("无法启动后端依赖安装程序。");
        var output = new StringBuilder();
        var stdout = ReadOutputAsync(process.StandardOutput, output, reportProgress);
        var stderr = ReadOutputAsync(process.StandardError, output, reportProgress);
        try
        {
            await process.WaitForExitAsync(cancellationToken);
            await Task.WhenAll(stdout, stderr);
        }
        catch (OperationCanceledException)
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            throw;
        }

        if (process.ExitCode != 0)
            throw new InvalidOperationException(LastLine(output) ?? "Python 后端依赖安装失败，请检查网络和安装日志。");
        reportProgress?.Invoke("Python 后端依赖安装完成");
    }

    private static async Task<string> FindPythonAsync(string backendRoot, CancellationToken cancellationToken)
    {
        var configured = new[]
        {
            Environment.GetEnvironmentVariable("FRC_PYTHON"),
            Environment.GetEnvironmentVariable("COURSE_HUB_PYTHON")
        }.Where(path => !string.IsNullOrWhiteSpace(path)).Cast<string>();
        foreach (var candidate in configured)
            if (await ProbePythonAsync(candidate, [], cancellationToken) is { } resolved) return resolved;

        var localCandidates = new[]
        {
            Path.Combine(backendRoot, ".venv", "Scripts", "python.exe"),
            Path.Combine(backendRoot, "runtime", "python", "python.exe")
        }.Where(File.Exists);

        foreach (var candidate in localCandidates)
            if (await ProbePythonAsync(candidate, [], cancellationToken) is { } resolved) return resolved;

        if (await ProbePythonAsync("python", [], cancellationToken) is { } pathPython) return pathPython;
        if (await ProbePythonAsync("py", ["-3"], cancellationToken) is { } launcherPython) return launcherPython;

        throw new InvalidOperationException("未找到 Python 3.10+。请安装 Python 并加入 PATH 后重试；安装 Python 运行环境需在应用外完成。");
    }

    private static async Task<string?> FindRuntimePythonAsync(string backendRoot, CancellationToken cancellationToken)
    {
        var configured = Environment.GetEnvironmentVariable("FRC_PYTHON")
            ?? Environment.GetEnvironmentVariable("COURSE_HUB_PYTHON");
        if (!string.IsNullOrWhiteSpace(configured))
            return await ProbePythonAsync(configured, [], cancellationToken);

        foreach (var candidate in new[]
        {
            Path.Combine(backendRoot, ".venv", "Scripts", "python.exe"),
            Path.Combine(backendRoot, "runtime", "python", "python.exe")
        })
        {
            if (!File.Exists(candidate)) continue;
            if (await ProbePythonAsync(candidate, [], cancellationToken) is { } resolved) return resolved;
        }
        return await ProbePythonAsync("python", [], cancellationToken);
    }

    private static async Task<bool> ProbeDependenciesAsync(string python, CancellationToken cancellationToken)
    {
        try
        {
            var startInfo = new ProcessStartInfo(python)
            {
                UseShellExecute = false,
                CreateNoWindow = true
            };
            startInfo.ArgumentList.Add("-c");
            startInfo.ArgumentList.Add(DependencyProbe);
            using var process = Process.Start(startInfo);
            if (process == null) return false;
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(5));
            try { await process.WaitForExitAsync(timeout.Token); }
            catch (OperationCanceledException)
            {
                if (!process.HasExited) process.Kill(entireProcessTree: true);
                throw;
            }
            return process.ExitCode == 0;
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) { return false; }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException or IOException) { return false; }
    }

    private static async Task<string?> ProbePythonAsync(string executable, IReadOnlyList<string> prefix,
        CancellationToken cancellationToken)
    {
        try
        {
            var startInfo = new ProcessStartInfo(executable)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            foreach (var argument in prefix) startInfo.ArgumentList.Add(argument);
            startInfo.ArgumentList.Add("-c");
            startInfo.ArgumentList.Add(PythonProbe);
            using var process = Process.Start(startInfo);
            if (process == null) return null;
            var stdout = process.StandardOutput.ReadToEndAsync();
            _ = process.StandardError.ReadToEndAsync();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(8));
            try { await process.WaitForExitAsync(timeout.Token); }
            catch (OperationCanceledException)
            {
                if (!process.HasExited) process.Kill(entireProcessTree: true);
                throw;
            }
            if (process.ExitCode != 0) return null;
            var lines = (await stdout).Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries);
            if (lines.Length < 2 || !int.TryParse(lines[1], NumberStyles.None, CultureInfo.InvariantCulture, out var version) || version < 31000)
                return null;
            return lines[0];
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) { return null; }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException or IOException) { return null; }
    }

    private static async Task ReadOutputAsync(StreamReader reader, StringBuilder output, Action<string>? reportProgress)
    {
        while (await reader.ReadLineAsync() is { } line)
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            lock (output)
            {
                if (output.Length > 12000) output.Remove(0, output.Length - 6000);
                output.AppendLine(line);
            }
            reportProgress?.Invoke(line);
        }
    }

    private static string? LastLine(StringBuilder output)
    {
        lock (output)
            return output.ToString().Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries).LastOrDefault();
    }
}
