using CommunityToolkit.Mvvm.ComponentModel;
using System.Collections.ObjectModel;

namespace FuckRubbishClass.Models;

public partial class VideoStudyConfig : ObservableObject
{
    [ObservableProperty]
    private double _playbackRate = 1.0;

    [ObservableProperty]
    private double _completionThreshold = 0.91;

    [ObservableProperty]
    private int _dailyLimitMinutes = 0;

    [ObservableProperty]
    private bool _isMuted = true;

    [ObservableProperty]
    private bool _autoNext = true;

    [ObservableProperty]
    private int _stallTimeoutSeconds = 120;

    [ObservableProperty]
    private string _browserType = "msedge";
}

public partial class HomeworkQuizConfig : ObservableObject
{
    [ObservableProperty]
    private bool _overwriteWithVerifiedAnswers = true;

    [ObservableProperty]
    private bool _autoRedoIfNotFullScore = true;

    [ObservableProperty]
    private bool _autoSubmit = false;

    [ObservableProperty]
    private bool _submitSafeguard = true;

    [ObservableProperty]
    private double _answerDelaySeconds = 1.5;

    [ObservableProperty]
    private bool _useImageBankMatch = true;

    [ObservableProperty]
    private bool _harvestAnswersToBank = true;
}

public partial class ExamAssessmentConfig : ObservableObject
{
    [ObservableProperty]
    private string _mode = "ReadonlyAssist"; // ReadonlyAssist or AutoExam

    [ObservableProperty]
    private bool _autoSubmitExam = false;

    [ObservableProperty]
    private int _captchaTimeoutSeconds = 300;

    [ObservableProperty]
    private double _answerDelaySeconds = 2.0;

    [ObservableProperty]
    private int _maxContentChars = 10000;

    [ObservableProperty]
    private bool _pauseOnUnknownQuestion = true;
}

public partial class SignInScheduleItem : ObservableObject
{
    [ObservableProperty]
    private string _id = Guid.NewGuid().ToString("N");

    [ObservableProperty]
    private string _courseName = string.Empty;

    [ObservableProperty]
    private string _classId = string.Empty;

    [ObservableProperty]
    private int _dayOfWeek = 1; // 1 = Monday

    [ObservableProperty]
    private string _startTime = "08:00";

    [ObservableProperty]
    private string _endTime = "09:40";

    [ObservableProperty]
    private bool _isEnabled = true;

    public string DayOfWeekDisplayName => DayOfWeek switch
    {
        0 => "星期日",
        1 => "星期一",
        2 => "星期二",
        3 => "星期三",
        4 => "星期四",
        5 => "星期五",
        6 => "星期六",
        _ => "未知"
    };

    public string TimeRangeDisplay => $"{StartTime} ~ {EndTime}";
}

public partial class AttendanceSignInConfig : ObservableObject
{
    [ObservableProperty]
    private bool _isServiceRunning;

    [ObservableProperty]
    private int _checkIntervalSeconds = 60;

    [ObservableProperty]
    private double _latitude = 24.5824;

    [ObservableProperty]
    private double _longitude = 118.0932;

    [ObservableProperty]
    private string _locationAddress = "集美大学";

    [ObservableProperty]
    private bool _enableGpsSpoofing = true;

    [ObservableProperty]
    private bool _enableNumericCodeDetection = true;

    [ObservableProperty]
    private bool _enableQrForwarding = true;

    [ObservableProperty]
    private ObservableCollection<SignInScheduleItem> _schedules = new();
}

public partial class AiCourseConfig : ObservableObject
{
    [ObservableProperty]
    private bool _enabled = true;

    [ObservableProperty]
    private bool _useBuiltinPlatformAi = false;

    [ObservableProperty]
    private string _courseId = string.Empty;

    [ObservableProperty]
    private string _classId = string.Empty;

    [ObservableProperty]
    private bool _parseCoursewarePpt = true;

    [ObservableProperty]
    private bool _deleteUploadedPptAfterParsing = true;
}
