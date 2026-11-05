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

public class PythonBridgeService : IPythonBridgeService
{
    private Process? _process;
    private StreamWriter? _writer;
    private StreamReader? _reader;
    private readonly SemaphoreSlim _lock = new(1, 1);
    public bool IsConnected => _process is { HasExited: false };

    public async Task<bool> EnsureConnectedAsync()
    {
        await _lock.WaitAsync();
        try
        {
            if (IsConnected) return true;

            var baseDir = AppDomain.CurrentDomain.BaseDirectory;
            // Search upward for desktop_bridge.py
            var current = new DirectoryInfo(baseDir);
            string? bridgeFile = null;
            while (current != null)
            {
                var candidate = Path.Combine(current.FullName, "desktop_bridge.py");
                if (File.Exists(candidate))
                {
                    bridgeFile = candidate;
                    break;
                }
                current = current.Parent;
            }

            if (bridgeFile == null)
            {
                // Standalone mode without external python bridge
                return false;
            }

            var root = Path.GetDirectoryName(bridgeFile)!;
            var pythonExe = "python";
            var venv = Path.Combine(root, "work", "venv", "Scripts", "python.exe");
            if (File.Exists(venv)) pythonExe = venv;

            var startInfo = new ProcessStartInfo
            {
                FileName = pythonExe,
                WorkingDirectory = root,
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardInputEncoding = new UTF8Encoding(false),
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            startInfo.ArgumentList.Add("-u");
            startInfo.ArgumentList.Add("desktop_bridge.py");

            _process = Process.Start(startInfo);
            if (_process != null)
            {
                _writer = _process.StandardInput;
                _reader = _process.StandardOutput;
                _ = _process.StandardError.ReadToEndAsync();
                return true;
            }
        }
        catch
        {
            // Bridge not reachable
        }
        finally
        {
            _lock.Release();
        }
        return false;
    }

    public async Task<JsonElement?> SendAsync(object request, Action<JsonElement>? onProgress = null, CancellationToken cancellationToken = default)
    {
        await _lock.WaitAsync(cancellationToken);
        try
        {
            if (!IsConnected && !await EnsureConnectedAsync()) return null;
            if (_writer == null || _reader == null) return null;

            var payload = JsonSerializer.Serialize(request);
            await _writer.WriteLineAsync(payload.AsMemory(), cancellationToken);
            await _writer.FlushAsync(cancellationToken);

            while (true)
            {
                var line = await _reader.ReadLineAsync(cancellationToken);
                if (line == null) return null;

                using var doc = JsonDocument.Parse(line);
                if (doc.RootElement.TryGetProperty("type", out var type) && type.GetString() == "progress")
                {
                    onProgress?.Invoke(doc.RootElement.Clone());
                    continue;
                }
                return doc.RootElement.Clone();
            }
        }
        finally
        {
            _lock.Release();
        }
    }

    public void Dispose()
    {
        try
        {
            _writer?.Dispose();
            _reader?.Dispose();
            if (_process != null && !_process.HasExited)
            {
                _process.Kill(true);
                _process.Dispose();
            }
        }
        catch
        {
            // Clean exit
        }
    }
}
