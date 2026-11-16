using CommunityToolkit.Mvvm.ComponentModel;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace FuckRubbishClass.Models;

public partial class CourseModel : ObservableObject
{
    [ObservableProperty]
    private string _id = Guid.NewGuid().ToString("N");

    [ObservableProperty]
    private string _courseId = string.Empty;

    [ObservableProperty]
    private string _classId = string.Empty;

    [ObservableProperty]
    private string _name = string.Empty;

    [ObservableProperty]
    private PlatformType _platform = PlatformType.Zhihuishu;

    [ObservableProperty]
    private string _accountId = string.Empty;

    [ObservableProperty]
    private string _accountDisplayName = string.Empty;

    [ObservableProperty]
    private string _teacher = string.Empty;

    [ObservableProperty]
    private string _school = string.Empty;

    [ObservableProperty]
    private double _progressPercent;

    [ObservableProperty]
    private string _modeLabel = "视频课程";

    [ObservableProperty]
    private bool _isAiCourse;

    [ObservableProperty]
    private int _pendingHomeworkCount;

    [ObservableProperty]
    private int _pendingExamCount;

    [ObservableProperty]
    private bool _isSelected;

    [ObservableProperty]
    private string _coverColor = "#2563EB";

    public JsonElement? RawMetadata { get; set; }

    [JsonIgnore]
    public string PlatformDisplayName => Platform switch
    {
        PlatformType.Zhihuishu => "智慧树",
        PlatformType.Chaoxing => "学习通",
        PlatformType.Ketangpai => "课堂派",
        _ => "其他"
    };

    [JsonIgnore]
    public string ProgressText => $"{ProgressPercent:F1}%";

    [JsonIgnore]
    public bool HasPendingTasks => PendingHomeworkCount > 0 || PendingExamCount > 0;
}
