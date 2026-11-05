using FuckRubbishClass.Models;

namespace FuckRubbishClass.Core.Logging;

/// <summary>
/// 全局统一静态日志入口，方便在无 DI 注入或静态工具类中直接调用
/// </summary>
public static class AppLogger
{
    private static ILoggerService? _instance;

    public static void Initialize(ILoggerService loggerService)
    {
        _instance = loggerService;
    }

    public static ILoggerService Instance => _instance ??= new LoggerService();

    public static void Log(LogLevel level, string category, string message, Exception? ex = null) =>
        Instance.Log(level, category, message, ex);

    public static void Debug(string category, string message) => Instance.Debug(category, message);
    public static void Info(string category, string message) => Instance.Info(category, message);
    public static void Success(string category, string message) => Instance.Success(category, message);
    public static void Warn(string category, string message, Exception? ex = null) => Instance.Warn(category, message, ex);
    public static void Error(string category, string message, Exception? ex = null) => Instance.Error(category, message, ex);
}
