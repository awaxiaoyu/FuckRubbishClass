using FuckRubbishClass.Core.Logging;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace FuckRubbishClass.Services;

public interface IPythonBridgeService : IDisposable
{
    bool IsConnected { get; }
    Task<bool> EnsureConnectedAsync();
    Task<JsonElement?> SendAsync(object request, Action<JsonElement>? onProgress = null, CancellationToken cancellationToken = default);
}

/// <summary>One JSONL connection per account; the Python main thread owns Playwright.</summary>
public sealed class PythonBridgeService : IPythonBridgeService
{
    private readonly ILoggerService _logger;
    private readonly string? _backendRoot;
    private readonly string? _dataDirectory;
    private readonly string? _pythonExecutable;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private Process? _process;
    private bool _disposed;

    public bool IsConnected => _process is { HasExited: false };

    public PythonBridgeService(ILoggerService? logger = null, string? backendRoot = null,
        string? dataDirectory = null, string? pythonExecutable = null)
    {
        _logger = logger ?? AppLogger.Instance;
        _backendRoot = backendRoot;
        _dataDirectory = dataDirectory;
        _pythonExecutable = pythonExecutable;
    }

    public static string FindBackendRoot()
    {
        var configured = Environment.GetEnvironmentVariable("FRC_BACKEND_ROOT");
        if (!string.IsNullOrWhiteSpace(configured)) return Path.GetFullPath(configured);
        string? bundled = null;
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "backend");
            if (!File.Exists(Path.Combine(candidate, "desktop_bridge.py"))) continue;
            bundled ??= candidate;
            if (File.Exists(Path.Combine(candidate, ".venv", "Scripts", "python.exe"))) return candidate;
        }
        if (bundled != null) return bundled;
        throw new DirectoryNotFoundException("未找到 backend/desktop_bridge.py，请检查发布目录或设置 FRC_BACKEND_ROOT。");
    }

    private async Task ConnectCoreAsync(CancellationToken token)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (IsConnected) return;
        StopProcess();
        var root = _backendRoot ?? FindBackendRoot();
        var data = _dataDirectory ?? Path.Combine(AppContext.BaseDirectory, "data", "backend");
        Directory.CreateDirectory(data);
        var python = _pythonExecutable ?? Environment.GetEnvironmentVariable("FRC_PYTHON")
            ?? Environment.GetEnvironmentVariable("COURSE_HUB_PYTHON");
        if (string.IsNullOrWhiteSpace(python))
        {
            python = new[] { Path.Combine(root, ".venv", "Scripts", "python.exe"),
                Path.Combine(root, "runtime", "python", "python.exe") }.FirstOrDefault(File.Exists) ?? "python";
        }
        var info = new ProcessStartInfo(python)
        {
            WorkingDirectory = data,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        info.ArgumentList.Add("-u");
        info.ArgumentList.Add(Path.Combine(root, "desktop_bridge.py"));
        info.Environment["PYTHONUTF8"] = "1";
        info.Environment["FRC_BACKEND_DATA_DIR"] = Path.GetFullPath(data);
        _process = Process.Start(info) ?? throw new IOException("无法启动 Python 后端。");
        _ = ReadDiagnosticsAsync(_process.StandardError);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(TimeSpan.FromSeconds(20));
        try
        {
            await ExchangeAsync(new { operation = "platforms" }, null, timeout.Token);
            _logger.Success("Bridge", $"后端协议握手完成 (PID: {_process.Id})。");
        }
        catch
        {
            StopProcess();
            throw;
        }
    }

    public async Task<bool> EnsureConnectedAsync()
    {
        await _gate.WaitAsync();
        try
        {
            await ConnectCoreAsync(CancellationToken.None);
            return true;
        }
        catch (Exception ex)
        {
            _logger.Error("Bridge", $"后端不可用：{ex.Message}。请检查 Python 与 backend/requirements-csharp-desktop.txt。", ex);
            return false;
        }
        finally { _gate.Release(); }
    }

    public async Task<JsonElement?> SendAsync(object request, Action<JsonElement>? onProgress = null,
        CancellationToken cancellationToken = default)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            await ConnectCoreAsync(cancellationToken);
            return await ExchangeAsync(request, onProgress, cancellationToken);
        }
        finally { _gate.Release(); }
    }

    private async Task<JsonElement> ExchangeAsync(object request, Action<JsonElement>? onProgress, CancellationToken token)
    {
        var process = _process ?? throw new IOException("后端未连接。");
        Task<string?>? pendingRead = null;
        var sent = false;
        try
        {
            token.ThrowIfCancellationRequested();
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(request));
            await process.StandardInput.FlushAsync();
            sent = true;
            while (true)
            {
                pendingRead = process.StandardOutput.ReadLineAsync();
                var line = await pendingRead.WaitAsync(token);
                pendingRead = null;
                if (line == null) throw new IOException("Python 后端已退出，请查看 Bridge 日志。");
                using var document = JsonDocument.Parse(line);
                var frame = document.RootElement;
                if (frame.TryGetProperty("stream", out var stream))
                {
                    if (stream.GetString() == "progress") onProgress?.Invoke(frame.GetProperty("event").Clone());
                    else if (stream.GetString() == "confirmation")
                    {
                        await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new
                        {
                            operation = "confirm_ai_only_submit_response",
                            confirmationId = frame.GetProperty("event").GetProperty("confirmationId").GetString(),
                            confirmed = false
                        }));
                        await process.StandardInput.FlushAsync();
                        _logger.Warn("Bridge", "存在仅由 AI 生成的答案，已保留草稿；请在平台检查后提交。");
                    }
                    continue;
                }
                if (!frame.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.True)
                    throw new BackendOperationException(frame.TryGetProperty("error", out var error)
                        ? error.GetString() ?? "后端操作失败。" : "后端响应缺少 ok 状态。");
                return frame.Clone();
            }
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            if (sent)
            {
                try
                {
                    using var drainTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                    await process.StandardInput.WriteLineAsync("{\"operation\":\"cancel\"}").WaitAsync(drainTimeout.Token);
                    await process.StandardInput.FlushAsync().WaitAsync(drainTimeout.Token);
                    while (true)
                    {
                        var line = await (pendingRead ?? process.StandardOutput.ReadLineAsync()).WaitAsync(drainTimeout.Token);
                        pendingRead = null;
                        if (line == null) throw new IOException("后端已退出。");
                        using var frame = JsonDocument.Parse(line);
                        if (!frame.RootElement.TryGetProperty("stream", out _)) break;
                    }
                }
                catch { StopProcess(); }
            }
            throw;
        }
        catch (BackendOperationException) { throw; }
        catch
        {
            StopProcess();
            throw;
        }
    }

    private async Task ReadDiagnosticsAsync(StreamReader reader)
    {
        try
        {
            while (await reader.ReadLineAsync() is { } line)
                if (!string.IsNullOrWhiteSpace(line)) _logger.Warn("Python", line);
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException) { }
    }

    private void StopProcess()
    {
        var process = Interlocked.Exchange(ref _process, null);
        if (process == null) return;
        try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
        catch (InvalidOperationException) { }
        finally { process.Dispose(); }
    }

    public void Dispose()
    {
        _disposed = true;
        StopProcess();
    }
}

public sealed class BackendOperationException(string message) : Exception(message);
