using CommunityToolkit.Mvvm.ComponentModel;

namespace FuckRubbishClass.Models;

public partial class TaskJobModel : ObservableObject
{
    [ObservableProperty]
    private string _id = Guid.NewGuid().ToString("N");

    [ObservableProperty]
    private string _title = "新建任务";

    [ObservableProperty]
    private FeatureType _type = FeatureType.VideoStudy;

    [ObservableProperty]
    private PlatformType _platform = PlatformType.Zhihuishu;

    [ObservableProperty]
    private string _accountName = string.Empty;

    [ObservableProperty]
    private string _courseName = string.Empty;

    [ObservableProperty]
    private TaskExecutionStatus _status = TaskExecutionStatus.Idle;

    [ObservableProperty]
    private double _progressPercent;

    [ObservableProperty]
    private string _currentStep = "准备就绪";

    [ObservableProperty]
    private DateTime _startTime = DateTime.Now;

    [ObservableProperty]
    private string _durationText = "00:00";

    public string TypeDisplayName => Type switch
    {
        FeatureType.VideoStudy => "视频自学",
        FeatureType.HomeworkQuiz => "作业测试",
        FeatureType.ExamAssessment => "考试中心",
        FeatureType.AttendanceSignIn => "签到监控",
        FeatureType.AiCourse => "AI 智能课",
        _ => "日常任务"
    };

    public string StatusBadgeColor => Status switch
    {
        TaskExecutionStatus.Running => "#2563EB",
        TaskExecutionStatus.Completed => "#10B981",
        TaskExecutionStatus.Failed => "#EF4444",
        TaskExecutionStatus.Paused => "#F59E0B",
        TaskExecutionStatus.Cancelled => "#64748B",
        _ => "#94A3B8"
    };
}

public partial class LogEntry : ObservableObject
{
    [ObservableProperty]
    private string _id = Guid.NewGuid().ToString("N");

    [ObservableProperty]
    private DateTime _timestamp = DateTime.Now;

    [ObservableProperty]
    private LogLevel _level = LogLevel.Info;

    [ObservableProperty]
    private string _source = "系统";

    [ObservableProperty]
    private string _message = string.Empty;

    public string TimeFormatted => Timestamp.ToString("HH:mm:ss.fff");

    public string LevelBadgeColor => Level switch
    {
        LogLevel.Success => "#10B981",
        LogLevel.Warning => "#F59E0B",
        LogLevel.Error => "#EF4444",
        LogLevel.Debug => "#8B5CF6",
        _ => "#3B82F6"
    };
}
