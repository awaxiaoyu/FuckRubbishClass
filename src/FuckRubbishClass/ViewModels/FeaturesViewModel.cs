using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace FuckRubbishClass.ViewModels;

public partial class FeaturesViewModel : ViewModelBase
{
    private readonly ISettingsService _settingsService;
    private readonly ITaskExecutionService _tasks;
    private readonly IAccountService _accounts;
    private readonly ICourseService _courses;
    private readonly IPlatformBackendService _backend;
    private CancellationTokenSource? _requestCancellation;
    private bool _refreshingTargets;

    public VideoStudyConfig VideoConfig => _settingsService.VideoConfig;
    public HomeworkQuizConfig HomeworkConfig => _settingsService.HomeworkConfig;
    public ExamAssessmentConfig ExamConfig => _settingsService.ExamConfig;
    public AttendanceSignInConfig SignInConfig => _settingsService.SignInConfig;
    public AiCourseConfig AiCourseConfig => _settingsService.AiCourseConfig;
    public ObservableCollection<AccountModel> FeatureAccounts { get; } = new();
    public ObservableCollection<CourseModel> AvailableCourses { get; } = new();
    public ObservableCollection<PlatformTaskItem> Homeworks { get; } = new();
    public ObservableCollection<PlatformTaskItem> Exams { get; } = new();
    public ObservableCollection<HomeworkAnswerItem> HomeworkAnswers { get; } = new();
    public bool HasAccounts => FeatureAccounts.Count > 0;
    public bool HasSelectedAccount => SelectedAccount != null;
    public bool HasSelectedCourse => SelectedCourse != null;
    public bool HasSelectedHomework => SelectedHomework != null;
    public bool HasSelectedExam => SelectedExam != null;
    public bool CanOpenPlatform => SelectedAccount?.Platform is PlatformType.Chaoxing or PlatformType.Ketangpai;
    public bool IsCourseSelectionVisible => ActiveFeatureTab != "signin";
    public string TargetHint => ActiveFeatureTab switch
    {
        "homework" => "智慧树平时测试：选择课程后读取测试列表，可处理单份或整门课程。",
        "exam" => "学习通：考试列表属于当前账号；在托管浏览器中完成验证码。",
        "signin" => "课堂派：为当前账号检查服务状态，再按下方课程时间表启动守护。",
        "aicourse" => "智慧树 / 知到：选择 AI 课程，或使用下方 Course ID 与 Class ID。",
        _ => "智慧树与学习通视频自学；列表仅显示当前账号的课程。"
    };

    [ObservableProperty] private string _activeFeatureTab = "video";
    [ObservableProperty] private AccountModel? _selectedAccount;
    [ObservableProperty] private CourseModel? _selectedCourse;
    [ObservableProperty] private PlatformTaskItem? _selectedHomework;
    [ObservableProperty] private PlatformTaskItem? _selectedExam;
    [ObservableProperty] private string _operationMessage = "选择账号和课程后开始。";
    [ObservableProperty] private string _homeworkResult = "读取测试列表后选择一份测试。";
    [ObservableProperty] private string _examAnalysisResult = "在托管浏览器中打开题目后，可提取题面并生成解析。";
    [ObservableProperty] private string _serviceStatusText = "尚未检查当前账号的服务状态";
    [ObservableProperty] private bool _useManualAiCourse;
    [ObservableProperty] private string _newScheduleCourseName = "";
    [ObservableProperty] private string _newScheduleClassId = "";
    [ObservableProperty] private int _newScheduleDayOfWeek = 1;
    [ObservableProperty] private string _newScheduleStartTime = "08:00";
    [ObservableProperty] private string _newScheduleEndTime = "09:40";

    public FeaturesViewModel(ISettingsService settings, ITaskExecutionService tasks,
        IAccountService accounts, ICourseService courses, IPlatformBackendService backend)
    {
        _settingsService = settings;
        _tasks = tasks;
        _accounts = accounts;
        _courses = courses;
        _backend = backend;
        accounts.Accounts.CollectionChanged += (_, _) => RefreshTargets();
        courses.Courses.CollectionChanged += (_, _) => RefreshCourseTargets();
        RefreshTargets();
    }

    partial void OnActiveFeatureTabChanged(string value)
    {
        ClearMessages();
        RefreshTargets();
        OnPropertyChanged(nameof(TargetHint));
        OnPropertyChanged(nameof(IsCourseSelectionVisible));
    }

    partial void OnSelectedAccountChanged(AccountModel? value)
    {
        if (_refreshingTargets) return;
        ClearMessages();
        OperationMessage = TargetHint;
        Homeworks.Clear();
        Exams.Clear();
        HomeworkAnswers.Clear();
        SelectedHomework = null;
        SelectedExam = null;
        ServiceStatusText = "尚未检查当前账号的服务状态";
        SignInConfig.IsServiceRunning = false;
        RefreshCourseTargets();
        OnPropertyChanged(nameof(HasSelectedAccount));
        OnPropertyChanged(nameof(CanOpenPlatform));
    }

    partial void OnSelectedCourseChanged(CourseModel? value)
    {
        Homeworks.Clear();
        HomeworkAnswers.Clear();
        SelectedHomework = null;
        OnPropertyChanged(nameof(HasSelectedCourse));
    }

    partial void OnSelectedHomeworkChanged(PlatformTaskItem? value) => OnPropertyChanged(nameof(HasSelectedHomework));
    partial void OnSelectedExamChanged(PlatformTaskItem? value) => OnPropertyChanged(nameof(HasSelectedExam));

    private void RefreshTargets()
    {
        var candidates = _accounts.Accounts.Where(a => ActiveFeatureTab switch
        {
            "homework" or "aicourse" => a.Platform == PlatformType.Zhihuishu,
            "exam" => a.Platform == PlatformType.Chaoxing,
            "signin" => a.Platform == PlatformType.Ketangpai,
            _ => a.Platform is PlatformType.Zhihuishu or PlatformType.Chaoxing
        }).ToList();
        var next = candidates.FirstOrDefault(a => a.Id == SelectedAccount?.Id)
            ?? candidates.FirstOrDefault(a => a.Id == _accounts.ActiveAccount?.Id)
            ?? candidates.FirstOrDefault(a => a.IsPrimary) ?? candidates.FirstOrDefault();
        _refreshingTargets = true;
        try
        {
            FeatureAccounts.Clear();
            foreach (var item in candidates) FeatureAccounts.Add(item);
            SelectedAccount = next;
        }
        finally { _refreshingTargets = false; }
        OnSelectedAccountChanged(next);
        OnPropertyChanged(nameof(HasAccounts));
    }

    private void RefreshCourseTargets()
    {
        var nextId = SelectedCourse?.Id;
        var courses = _courses.Courses.Where(c => c.AccountId == SelectedAccount?.Id &&
            (ActiveFeatureTab switch
            {
                "aicourse" => c.IsAiCourse,
                "homework" => !c.IsAiCourse,
                _ => true
            })).ToList();
        AvailableCourses.Clear();
        foreach (var course in courses) AvailableCourses.Add(course);
        SelectedCourse = courses.FirstOrDefault(c => c.Id == nextId) ?? courses.FirstOrDefault();
    }

    private Action<JsonElement> UiProgress()
    {
        var reporter = new Progress<JsonElement>(value =>
        {
            if (value.TryGetProperty("message", out var message)) OperationMessage = message.GetString() ?? "";
        });
        return value => ((IProgress<JsonElement>)reporter).Report(value);
    }

    private async Task PerformAsync(Func<CancellationToken, Task> work)
    {
        if (IsBusy) return;
        ClearMessages();
        IsBusy = true;
        using var cancellation = new CancellationTokenSource();
        _requestCancellation = cancellation;
        try { await work(cancellation.Token); }
        catch (OperationCanceledException) { OperationMessage = "操作已取消"; }
        catch (Exception ex) { ErrorMessage = ex.Message; OperationMessage = ex.Message; }
        finally { _requestCancellation = null; IsBusy = false; }
    }

    [RelayCommand] private void CancelOperation() => _requestCancellation?.Cancel();
    public void CancelPendingOperation() => _requestCancellation?.Cancel();
    [RelayCommand] private void OpenAccounts() => AppEventBus.Publish(new NavigationRequestedEvent("accounts"));
    [RelayCommand] private void OpenTaskMonitor() => AppEventBus.Publish(new NavigationRequestedEvent("tasks"));

    [RelayCommand]
    private async Task RefreshSelectedCoursesAsync()
    {
        if (SelectedAccount is not { } account) return;
        await PerformAsync(async token =>
        {
            await _courses.RefreshCoursesForAccountAsync(account.Id, token);
            SuccessMessage = $"已同步 {AvailableCourses.Count} 门课程";
        });
    }

    [RelayCommand]
    private async Task OpenPlatformAsync()
    {
        if (SelectedAccount is not { } account) return;
        await PerformAsync(async token =>
        {
            await _backend.InvokeAsync(account, "open_platform", token: token);
            OperationMessage = "平台窗口已打开";
        });
    }

    private static object CoursePayload(CourseModel course) =>
        new { courseId = course.CourseId, name = course.Name, metadata = course.RawMetadata };

    [RelayCommand] private Task StartVideoStudyAsync() => QueueCourseAsync(FeatureType.VideoStudy);
    [RelayCommand] private Task StartHomeworkQuizAsync() => QueueCourseAsync(FeatureType.HomeworkQuiz);

    private async Task QueueCourseAsync(FeatureType feature, CourseModel? requested = null)
    {
        ClearMessages();
        var course = requested ?? SelectedCourse;
        var account = SelectedAccount;
        if (course == null || account == null) { ErrorMessage = "请先选择账号并同步课程。"; return; }
        if (course.AccountId != account.Id) { ErrorMessage = "课程与账号不匹配，请重新选择。"; return; }
        var job = NewJob(account, course.Name, feature, course);
        await _tasks.StartJobAsync(job, (token, progress, step) =>
            _backend.RunCourseAsync(account, course, feature, progress, step, token));
        SuccessMessage = "任务已加入队列，可在任务监控中查看进度或停止。";
    }

    private static TaskJobModel NewJob(AccountModel account, string title, FeatureType feature, CourseModel? course = null) => new()
    {
        Title = title, Type = feature, Platform = account.Platform, AccountId = account.Id,
        AccountName = account.DisplayTitle, CourseId = course?.CourseId ?? "", CourseName = course?.Name ?? title
    };

    [RelayCommand]
    private async Task LoadHomeworksAsync()
    {
        if (SelectedAccount is not { } account || SelectedCourse is not { } course) return;
        await PerformAsync(async token =>
        {
            OperationMessage = "正在读取当前课程的平时测试…";
            var result = await _backend.InvokeAsync(account, "list_homeworks",
                new { course = CoursePayload(course) }, UiProgress(), token);
            // Ignore results if the user changed targets while the request was running.
            if (SelectedAccount?.Id != account.Id || SelectedCourse?.Id != course.Id) return;
            Homeworks.Clear();
            foreach (var row in result.GetProperty("homeworks").EnumerateArray())
                Homeworks.Add(new PlatformTaskItem(Text(row, "name"),
                    $"{Text(row, "chapterName")}  截止：{Text(row, "deadline")}", row.Clone()));
            SelectedHomework = Homeworks.FirstOrDefault();
            OperationMessage = Homeworks.Count == 0 ? "当前课程没有可读取的平时测试" : $"已读取 {Homeworks.Count} 份测试";
        });
    }

    [RelayCommand]
    private async Task RunSelectedHomeworkAsync()
    {
        if (SelectedAccount is not { } account || SelectedHomework is not { } homework) return;
        var course = SelectedCourse;
        await _tasks.StartJobAsync(NewJob(account, homework.Title, FeatureType.HomeworkQuiz, course),
            async (token, progress, step) =>
            {
                var response = await _backend.InvokeAsync(account, "run_homework", new
                {
                    homework = homework.Payload,
                    autoSubmit = HomeworkConfig.AutoSubmit && !HomeworkConfig.SubmitSafeguard,
                    autoRedo = HomeworkConfig.AutoRedoIfNotFullScore,
                    overwriteVerifiedAnswers = HomeworkConfig.OverwriteWithVerifiedAnswers
                }, value => PlatformBackendService.ReportProgress(value, progress, step), token);
                var result = response.GetProperty("result");
                var submitted = result.TryGetProperty("submitted", out var submittedValue) && submittedValue.ValueKind == JsonValueKind.True;
                var message = submitted ? "测试处理结束，后端报告已提交。" : "测试处理结束，未提交；请在平台检查草稿。";
                await OnUiAsync(() => HomeworkResult = message);
                if (result.TryGetProperty("failed", out var failed) &&
                    ((failed.ValueKind == JsonValueKind.Object && failed.EnumerateObject().Any()) ||
                     (failed.ValueKind == JsonValueKind.Number && failed.GetInt32() > 0)))
                    throw new BackendOperationException("仍有题目作答失败，请查看任务日志。");
            });
        SuccessMessage = "已加入单份测试任务。";
    }

    [RelayCommand] private Task FetchHomeworkAnswersAsync() => ReadAnswersAsync(false);
    [RelayCommand] private Task HarvestHomeworkAnswersAsync() => ReadAnswersAsync(true);

    private async Task ReadAnswersAsync(bool harvest)
    {
        if (SelectedAccount is not { } account || SelectedHomework is not { } homework) return;
        await PerformAsync(async token =>
        {
            var response = await _backend.InvokeAsync(account,
                harvest ? "harvest_homework_answers" : "fetch_homework_answers",
                new { homework = homework.Payload }, UiProgress(), token);
            if (SelectedAccount?.Id != account.Id || SelectedHomework != homework) return;
            var result = response.GetProperty("result");
            HomeworkAnswers.Clear();
            foreach (var item in result.GetProperty("items").EnumerateArray())
                HomeworkAnswers.Add(new HomeworkAnswerItem(Text(item, "question"),
                    string.IsNullOrWhiteSpace(Text(item, "letters")) ? "未识别到答案" : $"识别答案：{Text(item, "letters")}",
                    Text(item, "error"), DecodeImage(Text(item, "imageBase64"))));
            HomeworkResult = harvest
                ? $"已采集 {Text(result, "saved")} 题，跳过 {Text(result, "skipped")} 题。"
                : $"已读取 {HomeworkAnswers.Count} 道题的答案信息。";
        });
    }

    [RelayCommand]
    private async Task LoadExamsAsync()
    {
        if (SelectedAccount is not { } account) return;
        await PerformAsync(async token =>
        {
            OperationMessage = "正在读取当前学习通账号的考试列表…";
            var result = await _backend.InvokeAsync(account, "list_exams", onProgress: UiProgress(), token: token);
            if (SelectedAccount?.Id != account.Id) return;
            Exams.Clear();
            foreach (var row in result.GetProperty("exams").EnumerateArray())
                Exams.Add(new PlatformTaskItem(Text(row, "title"),
                    $"{Text(row, "courseName")}  {Text(row, "statusText")}  截止：{Text(row, "endTime")}", row.Clone()));
            SelectedExam = Exams.FirstOrDefault();
            OperationMessage = Exams.Count == 0 ? "当前账号没有可读取的考试" : $"已读取 {Exams.Count} 场考试";
        });
    }

    [RelayCommand]
    private async Task RunSelectedExamAsync()
    {
        if (SelectedAccount is not { } account || SelectedExam is not { } exam) return;
        ExamConfig.Mode = "AutoExam";
        var job = NewJob(account, exam.Title, FeatureType.ExamAssessment);
        await _tasks.StartJobAsync(job, async (token, progress, step) =>
        {
            step.Report("正在打开所选考试；验证码需在浏览器完成。");
            var response = await _backend.InvokeAsync(account, "run_exam", exam.Payload,
                value => PlatformBackendService.ReportProgress(value, progress, step), token);
            var result = response.GetProperty("result");
            if (result.TryGetProperty("failed", out var failed) &&
                ((failed.ValueKind == JsonValueKind.Number && failed.GetInt32() > 0) ||
                 (failed.ValueKind == JsonValueKind.Array && failed.GetArrayLength() > 0)))
                throw new BackendOperationException("考试处理结束，仍有失败题目，请检查浏览器与任务日志。");
        });
        SuccessMessage = "考试任务已加入队列，验证码请在浏览器中完成。";
    }

    [RelayCommand]
    private async Task RunExamReadonlyAnalysisAsync()
    {
        if (SelectedAccount is not { } account) return;
        await PerformAsync(async token =>
        {
            var response = await _backend.InvokeAsync(account, "explain_question", new
            {
                mode = "explain", referenceNotes = Array.Empty<string>(), userCandidate = "",
                consent = new { currentPageOnly = true, noSessionData = true }
            }, UiProgress(), token);
            var result = response.GetProperty("result");
            var text = new StringBuilder(Text(result, "explanation"));
            foreach (var key in new[] { "concepts", "steps", "ambiguities", "citations" })
                if (result.TryGetProperty(key, out var lines) && lines.ValueKind == JsonValueKind.Array && lines.GetArrayLength() > 0)
                {
                    text.AppendLine().AppendLine();
                    text.AppendLine(key switch { "concepts" => "相关概念", "steps" => "解题步骤", "ambiguities" => "待确认内容", _ => "参考资料" });
                    foreach (var line in lines.EnumerateArray()) text.AppendLine(line.GetString());
                }
            ExamAnalysisResult = text.ToString();
        });
    }

    [RelayCommand] private Task RefreshServiceStatusAsync() => ServiceOperationAsync("service_status");
    [RelayCommand] private Task InstallSignInDependenciesAsync() => ServiceOperationAsync("install_dependencies");
    [RelayCommand] private Task OpenSignInLogsAsync() => ServiceOperationAsync("open_logs");
    [RelayCommand]
    private async Task ToggleSignInServiceAsync()
    {
        if (SelectedAccount is not { } account) return;
        await PerformAsync(async token =>
        {
            var before = await _backend.InvokeAsync(account, "service_status", token: token);
            var running = before.GetProperty("status").GetProperty("running").GetBoolean();
            await _backend.InvokeAsync(account, running ? "stop_service" : "start_service", onProgress: UiProgress(), token: token);
            await ReadServiceStatusAsync(account, token);
        });
    }

    private async Task ServiceOperationAsync(string operation)
    {
        if (SelectedAccount is not { } account) return;
        await PerformAsync(async token =>
        {
            if (operation != "service_status")
                await _backend.InvokeAsync(account, operation, onProgress: UiProgress(), token: token);
            await ReadServiceStatusAsync(account, token);
        });
    }

    private async Task ReadServiceStatusAsync(AccountModel account, CancellationToken token)
    {
        var response = await _backend.InvokeAsync(account, "service_status", token: token);
        if (SelectedAccount?.Id != account.Id) return;
        var status = response.GetProperty("status");
        SignInConfig.IsServiceRunning = status.GetProperty("running").GetBoolean();
        ServiceStatusText = $"{(SignInConfig.IsServiceRunning ? "服务运行中" : "服务已停止")} · " +
            $"Node {(status.GetProperty("runtimeAvailable").GetBoolean() ? "就绪" : "未就绪")} · " +
            $"依赖{(status.GetProperty("dependencies").GetBoolean() ? "已安装" : "未安装")}" +
            (status.GetProperty("configured").GetBoolean() ? "" : $" · {Text(status, "configurationIssue")}");
    }

    [RelayCommand]
    private async Task AddSignInScheduleAsync()
    {
        ClearMessages();
        if (string.IsNullOrWhiteSpace(NewScheduleCourseName) || string.IsNullOrWhiteSpace(NewScheduleClassId) ||
            !TimeOnly.TryParseExact(NewScheduleStartTime, "HH:mm", out var start) ||
            !TimeOnly.TryParseExact(NewScheduleEndTime, "HH:mm", out var end) || end <= start ||
            NewScheduleDayOfWeek is < 0 or > 6)
        { ErrorMessage = "请填写课程名称、Class ID、星期及有效的 HH:mm 起止时间。"; return; }
        if (SignInConfig.Schedules.Any(s => s.DayOfWeek == NewScheduleDayOfWeek &&
            TimeOnly.TryParseExact(s.StartTime, "HH:mm", out var oldStart) &&
            TimeOnly.TryParseExact(s.EndTime, "HH:mm", out var oldEnd) && start <= oldEnd && end >= oldStart))
        { ErrorMessage = "同一天的监听时间不能重叠。"; return; }
        SignInConfig.Schedules.Add(new SignInScheduleItem
        {
            CourseName = NewScheduleCourseName.Trim(), ClassId = NewScheduleClassId.Trim(),
            DayOfWeek = NewScheduleDayOfWeek, StartTime = NewScheduleStartTime, EndTime = NewScheduleEndTime
        });
        NewScheduleCourseName = "";
        NewScheduleClassId = "";
        await _settingsService.SaveSettingsAsync();
        SuccessMessage = "已保存课程时间表；服务运行中修改后需重新启动。";
    }

    [RelayCommand]
    private async Task DeleteSignInScheduleAsync(SignInScheduleItem? item)
    {
        if (item == null) return;
        SignInConfig.Schedules.Remove(item);
        await _settingsService.SaveSettingsAsync();
    }

    [RelayCommand]
    private async Task StartAiCourseAsync()
    {
        if (!UseManualAiCourse && SelectedCourse != null) { await QueueCourseAsync(FeatureType.AiCourse); return; }
        if (SelectedAccount is not { } account ||
            !long.TryParse(AiCourseConfig.CourseId, out var nativeId) || nativeId <= 0 ||
            !long.TryParse(AiCourseConfig.ClassId, out var classId) || classId <= 0)
        { ErrorMessage = "请选择知到 AI 课程，或填写有效的 Course ID / Class ID。"; return; }
        await QueueCourseAsync(FeatureType.AiCourse, new CourseModel
        {
            Platform = PlatformType.Zhihuishu, AccountId = account.Id, CourseId = $"zhidao_ai:{nativeId}:{classId}",
            ClassId = classId.ToString(), Name = $"知到 AI 课程 {nativeId}", IsAiCourse = true,
            RawMetadata = JsonSerializer.SerializeToElement(new { kind = "zhidao_ai", native_id = nativeId, class_id = classId })
        });
    }

    [RelayCommand]
    private async Task SaveAllConfigsAsync()
    {
        await _settingsService.SaveSettingsAsync();
        SuccessMessage = "功能参数已保存。";
    }

    private static string Text(JsonElement value, string key) =>
        value.TryGetProperty(key, out var text) && text.ValueKind != JsonValueKind.Null ? text.ToString() : "";

    private static ImageSource? DecodeImage(string encoded)
    {
        if (string.IsNullOrWhiteSpace(encoded)) return null;
        try
        {
            using var stream = new MemoryStream(Convert.FromBase64String(encoded));
            var image = new BitmapImage();
            image.BeginInit(); image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = stream; image.EndInit(); image.Freeze();
            return image;
        }
        catch (Exception ex) when (ex is FormatException or NotSupportedException or IOException) { return null; }
    }

    private static Task OnUiAsync(Action action)
    {
        if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
            return dispatcher.InvokeAsync(action).Task;
        action();
        return Task.CompletedTask;
    }
}
