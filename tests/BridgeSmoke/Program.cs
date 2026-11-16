using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;
using System.IO;
using System.Text.Json;

if (args.Length != 2) throw new ArgumentException("Usage: BridgeSmoke <backend root> <python executable>");
var backendRoot = Path.GetFullPath(args[0]);
var python = Path.GetFullPath(args[1]);
var temporary = Path.Combine(Path.GetTempPath(), "frc-bridge-smoke-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(temporary);
var logger = new QuietLogger();
void Check(bool condition, string message) { if (!condition) throw new Exception(message); }
async Task MustFail<T>(Func<Task> work) where T : Exception
{
    try { await work(); }
    catch (T) { return; }
    throw new Exception($"Expected {typeof(T).Name}");
}
try
{
    var fake = Path.Combine(AppContext.BaseDirectory, "fake_backend");
    using var first = new PythonBridgeService(logger, fake, Path.Combine(temporary, "first"), python);
    using var second = new PythonBridgeService(logger, fake, Path.Combine(temporary, "second"), python);
    var progress = new List<JsonElement>();
    // Send without a prior EnsureConnected used to deadlock recursively on the same semaphore.
    var response = await first.SendAsync(new { operation = "progress" }, progress.Add).WaitAsync(TimeSpan.FromSeconds(10));
    Check(progress.Count == 1 && progress[0].GetProperty("current").GetInt32() == 1, "Progress envelope was lost");
    var firstPid = response!.Value.GetProperty("pid").GetInt32();
    var other = await second.SendAsync(new { operation = "platforms" });
    Check(firstPid != other!.Value.GetProperty("pid").GetInt32(), "Accounts share a process");
    Check(response.Value.GetProperty("data").GetString() != other.Value.GetProperty("data").GetString(), "Accounts share data");
    var confirmation = await first.SendAsync(new { operation = "confirm" });
    Check(!confirmation!.Value.GetProperty("confirmed").GetBoolean(), "Unconfirmed submission was accepted");
    await MustFail<BackendOperationException>(() => first.SendAsync(new { operation = "error" }));
    var started = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    using (var cancel = new CancellationTokenSource())
    {
        var work = first.SendAsync(new { operation = "cancel_test" }, _ => started.TrySetResult(), cancel.Token);
        await started.Task.WaitAsync(TimeSpan.FromSeconds(5));
        cancel.Cancel();
        await MustFail<OperationCanceledException>(() => work);
    }
    var afterCancel = await first.SendAsync(new { operation = "platforms" });
    Check(afterCancel!.Value.GetProperty("pid").GetInt32() == firstPid, "Cooperative cancellation killed the browser process");
    Check(afterCancel.Value.GetProperty("operation").GetString() == "platforms", "A stale response contaminated the next request");
    using (var cancel = new CancellationTokenSource())
    {
        var ready = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var work = first.SendAsync(new { operation = "hang" }, _ => ready.TrySetResult(), cancel.Token);
        await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));
        cancel.Cancel();
        await MustFail<OperationCanceledException>(() => work.WaitAsync(TimeSpan.FromSeconds(8)));
    }
    Check(!first.IsConnected, "An unresponsive backend survived cancellation");
    await first.SendAsync(new { operation = "platforms" });
    await MustFail<JsonException>(() => first.SendAsync(new { operation = "invalid" }));
    Check(!first.IsConnected, "Invalid protocol did not reset the connection");
    await first.SendAsync(new { operation = "platforms" });
    Console.WriteLine("PASS: startup, progress, account isolation, confirmation, error, cancel/drain, forced stop and reconnect");

    // Exercise the real migrated backend without contacting any learning platform.
    using var real = new PythonBridgeService(logger, backendRoot, Path.Combine(temporary, "real"), python);
    var platforms = await real.SendAsync(new { operation = "platforms" });
    Check(platforms!.Value.GetProperty("platforms").GetArrayLength() == 3, "A platform is missing");
    await real.SendAsync(new { operation = "save_settings", settings = new { general = new { speed = 1.0 } } });
    Check(File.Exists(Path.Combine(temporary, "real", "config.json")), "Settings escaped the account data directory");
    Console.WriteLine("PASS: real backend handshake, platform discovery and settings isolation");

    using (var automatic = new PythonBridgeService(logger))
        Check(await automatic.EnsureConnectedAsync(), "Automatic backend/.venv discovery failed");
    Console.WriteLine("PASS: automatic project backend and Python runtime discovery");

    Environment.SetEnvironmentVariable("FRC_BACKEND_ROOT", backendRoot);
    Environment.SetEnvironmentVariable("FRC_PYTHON", python);
    var settings = new SettingsService(temporary, logger, new QuietEvents());
    var models = new ModelSchedulerService(temporary, logger, new QuietEvents());
    using var gateway = new PlatformBackendService(settings, models, logger);
    foreach (var platform in new[] { PlatformType.Zhihuishu, PlatformType.Chaoxing, PlatformType.Ketangpai })
    {
        var account = new AccountModel { Platform = platform };
        var result = await gateway.InvokeAsync(account, "platforms");
        Check(result.GetProperty("platforms").GetArrayLength() == 3, "Desktop settings failed backend validation");
    }
    var metadata = JsonSerializer.SerializeToElement(new { native_id = "1", class_id = "2", cpi = "3" });
    var roundTrip = JsonSerializer.Deserialize<CourseModel>(JsonSerializer.Serialize(new CourseModel { RawMetadata = metadata }));
    Check(roundTrip!.RawMetadata!.Value.GetProperty("cpi").GetString() == "3", "Course metadata did not survive a restart");
    Console.WriteLine("PASS: desktop settings mapping for all platforms and course metadata persistence");

    var tasks = new TaskExecutionService(logger, new QuietEvents()) { MaxConcurrency = 1 };
    var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var running = new TaskJobModel { AccountId = "same" };
    var queued = new TaskJobModel { AccountId = "same" };
    var queuedRan = false;
    await tasks.StartJobAsync(running, async (_, _, _) => { entered.SetResult(); await release.Task; });
    await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
    await tasks.StartJobAsync(queued, (_, _, _) => { queuedRan = true; return Task.CompletedTask; });
    tasks.CancelJob(queued.Id);
    await WaitUntil(() => queued.Status == TaskExecutionStatus.Cancelled);
    Check(!queuedRan, "Cancelled queued task still executed");
    release.SetResult();
    await WaitUntil(() => running.Status == TaskExecutionStatus.Completed && tasks.FinishedJobs.Count == 2);
    Check(tasks.FinishedJobs.Count(j => j.Id == queued.Id) == 1, "Cancelled task was inserted twice");
    Console.WriteLine("PASS: queued cancellation preserves task and semaphore ownership");
    Console.WriteLine("All smoke checks passed.");
}
finally
{
    // This directory was created above using a fixed prefix and a fresh GUID.
    Directory.Delete(temporary, recursive: true);
}

static async Task WaitUntil(Func<bool> condition)
{
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    while (!condition()) await Task.Delay(10, timeout.Token);
}

sealed class QuietEvents : IEventBus
{
    public void Publish<T>(T value) where T : class { }
    public void Subscribe<T>(object recipient, Action<T> handler, bool dispatchOnUi = true) where T : class { }
    public void Unsubscribe<T>(object recipient) where T : class { }
    public void UnsubscribeAll(object recipient) { }
}

sealed class QuietLogger : ILoggerService
{
    public ObservableCollection<LogEntry> LiveLogs { get; } = new();
    public void Log(LogLevel level, string category, string message, Exception? exception = null, object? payload = null) { }
    public void Debug(string category, string message) { }
    public void Info(string category, string message) { }
    public void Success(string category, string message) { }
    public void Warn(string category, string message, Exception? exception = null) { }
    public void Error(string category, string message, Exception? exception = null) { }
    public void Clear() { }
    public Task ExportLogsAsync(string path) => Task.CompletedTask;
    public string GetCurrentLogFilePath() => "";
}
