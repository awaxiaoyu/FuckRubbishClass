using FuckRubbishClass.Models;

namespace FuckRubbishClass.Core.Events;

public enum NotificationSeverity
{
    Info,
    Success,
    Warning,
    Error
}

// ---------------- 账号事件 ----------------
public record AccountLoggedInEvent(AccountModel Account);
public record AccountStateChangedEvent(AccountModel Account, string Reason);
public record PrimaryAccountChangedEvent(AccountModel Account);
public record AccountDeletedEvent(string AccountId);

// ---------------- 课程事件 ----------------
public record CoursesRefreshedEvent(int TotalCount, string AccountId);
public record CourseProgressUpdatedEvent(string CourseId, double NewProgress);

// ---------------- 任务执行事件 ----------------
public record TaskJobStartedEvent(TaskJobModel Job);
public record TaskJobProgressEvent(string JobId, double Progress, string CurrentStep);
public record TaskJobCompletedEvent(TaskJobModel Job, bool IsSuccess, string? ErrorMessage = null);
public record TaskJobCancelledEvent(string JobId);

// ---------------- 模型调用与调度容灾事件 ----------------
public record ModelInvocationStartedEvent(string ModelId, ModelRole Role, string RequestSummary);
public record ModelInvocationCompletedEvent(string ModelId, ModelRole Role, long LatencyMs, bool Success);
public record ModelFallbackTriggeredEvent(ModelRole Role, string FailedModelId, string FallbackModelId, string Reason);
public record ModelHealthTestedEvent(string ModelId, bool IsHealthy, long LatencyMs, string Status);

// ---------------- 页面导航与系统通知事件 ----------------
public record NavigationRequestedEvent(string PageKey, object? Parameter = null);
public record NotificationEvent(string Title, string Message, NotificationSeverity Severity = NotificationSeverity.Info);
public record ConfigSavedEvent(string SectionName);
