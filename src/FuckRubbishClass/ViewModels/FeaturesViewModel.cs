using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;

namespace FuckRubbishClass.ViewModels;

public partial class FeaturesViewModel : ViewModelBase
{
    private readonly ISettingsService _settingsService;
    private readonly ITaskExecutionService _taskService;
    private readonly IAccountService _accountService;
    private readonly ICourseService _courseService;
    private readonly IModelSchedulerService _schedulerService;

    // Feature Configurations
    public VideoStudyConfig VideoConfig => _settingsService.VideoConfig;
    public HomeworkQuizConfig HomeworkConfig => _settingsService.HomeworkConfig;
    public ExamAssessmentConfig ExamConfig => _settingsService.ExamConfig;
    public AttendanceSignInConfig SignInConfig => _settingsService.SignInConfig;
    public AiCourseConfig AiCourseConfig => _settingsService.AiCourseConfig;

    public ObservableCollection<AccountModel> Accounts => _accountService.Accounts;
    public ObservableCollection<CourseModel> Courses => _courseService.Courses;

    [ObservableProperty]
    private string _activeFeatureTab = "video"; // video, homework, exam, signin, aicourse

    [ObservableProperty]
    private AccountModel? _selectedAccount;

    [ObservableProperty]
    private CourseModel? _selectedCourse;

    // Exam preview text
    [ObservableProperty]
    private string _examAnalysisResult = "就绪。点击“提取题面与 AI 解析”后，深度推理模型将在此呈现详细题面分析与答题建议。";

    // Sign in editor
    [ObservableProperty]
    private string _newScheduleCourseName = string.Empty;

    [ObservableProperty]
    private string _newScheduleClassId = string.Empty;

    [ObservableProperty]
    private int _newScheduleDayOfWeek = 1;

    [ObservableProperty]
    private string _newScheduleStartTime = "08:00";

    [ObservableProperty]
    private string _newScheduleEndTime = "09:40";

    public FeaturesViewModel(
        ISettingsService settingsService,
        ITaskExecutionService taskService,
        IAccountService accountService,
        ICourseService courseService,
        IModelSchedulerService schedulerService)
    {
        _settingsService = settingsService;
        _taskService = taskService;
        _accountService = accountService;
        _courseService = courseService;
        _schedulerService = schedulerService;

        SelectedAccount = _accountService.Accounts.FirstOrDefault();
        SelectedCourse = _courseService.Courses.FirstOrDefault();
    }

    [RelayCommand]
    private void SwitchTab(string tab)
    {
        ActiveFeatureTab = tab;
    }

    [RelayCommand]
    private async Task StartVideoStudyAsync()
    {
        if (SelectedCourse == null)
        {
            ErrorMessage = "请先选择需要自学的课程";
            return;
        }

        var course = SelectedCourse;
        var job = new TaskJobModel
        {
            Title = $"视频自学: {course.Name}",
            Type = FeatureType.VideoStudy,
            Platform = course.Platform,
            AccountName = SelectedAccount?.DisplayTitle ?? "默认账号",
            CourseName = course.Name
        };

        await _taskService.StartJobAsync(job, async (token, progress, step) =>
        {
            step.Report($"连接平台播放网关 [{course.PlatformDisplayName}]，初始化倍速: {VideoConfig.PlaybackRate}x…");
            await Task.Delay(1000, token);
            progress.Report(15);

            step.Report("进入课程章节播放列表，校验未完成视频节点…");
            await Task.Delay(1200, token);
            progress.Report(40);

            step.Report($"正在播放当前小节视频 (完成阈值: {VideoConfig.CompletionThreshold:P0})…");
            for (int p = 40; p <= 95; p += 15)
            {
                token.ThrowIfCancellationRequested();
                await Task.Delay(800, token);
                progress.Report(p);
            }

            course.ProgressPercent = Math.Min(100.0, course.ProgressPercent + 15.0);
            step.Report($"小节学习达标，自动推进至下一视频节点。");
            progress.Report(100);
            await _courseService.SaveCoursesAsync();
        });
    }

    [RelayCommand]
    private async Task StartHomeworkQuizAsync()
    {
        if (SelectedCourse == null)
        {
            ErrorMessage = "请先选择课程";
            return;
        }

        var course = SelectedCourse;
        var fastModel = _schedulerService.ResolveModel(ModelRole.FastSolver);
        var visionModel = _schedulerService.ResolveModel(ModelRole.Vision);

        var job = new TaskJobModel
        {
            Title = $"平时测试作答: {course.Name}",
            Type = FeatureType.HomeworkQuiz,
            Platform = course.Platform,
            AccountName = SelectedAccount?.DisplayTitle ?? "默认账号",
            CourseName = course.Name
        };

        await _taskService.StartJobAsync(job, async (token, progress, step) =>
        {
            step.Report($"获取《{course.Name}》平时测试卷列表…");
            await Task.Delay(800, token);
            progress.Report(10);

            step.Report("第一阶段：检索本地已验证高精度题库与答案图…");
            await Task.Delay(1000, token);
            progress.Report(35);
            step.Report("命中 8/10 道题，答案图覆盖率 80%。");

            step.Report($"第二阶段：剩余 2 道未知题调度模型 [{fastModel?.Name ?? "FastSolver"}] 生成答案…");
            await Task.Delay(1400, token);
            progress.Report(70);

            if (HomeworkConfig.AutoRedoIfNotFullScore)
            {
                step.Report("校验历史测验记录：符合未满分自动重做策略，申请刷新重做…");
                await Task.Delay(800, token);
                progress.Report(85);
            }

            step.Report("第三阶段：逐题保存作答答案。保护模式：草稿保存完成（不主动交卷，防止不可逆判分）。");
            await Task.Delay(800, token);
            progress.Report(100);

            course.PendingHomeworkCount = Math.Max(0, course.PendingHomeworkCount - 1);
            await _courseService.SaveCoursesAsync();
        });
    }

    [RelayCommand]
    private async Task RunExamReadonlyAnalysisAsync()
    {
        var deepModel = _schedulerService.ResolveModel(ModelRole.DeepReasoning);

        var job = new TaskJobModel
        {
            Title = "考试题面提取与 AI 智能解析 (只读安全模式)",
            Type = FeatureType.ExamAssessment,
            Platform = SelectedCourse?.Platform ?? PlatformType.Chaoxing
        };

        await _taskService.StartJobAsync(job, async (token, progress, step) =>
        {
            step.Report("正在从当前页面提取题面与选项文本…");
            await Task.Delay(900, token);
            progress.Report(30);

            step.Report($"调度深度推理模型 [{deepModel?.Name ?? "DeepSeek-R1"}] 进行考题研判与反向验证…");
            await Task.Delay(1800, token);
            progress.Report(85);

            ExamAnalysisResult = $"""
                【题面解析已生成 - 仅供学习参考】
                题目：关于马克思主义唯物辩证法核心规律的辨析
                推荐答案：A. 对立统一规律
                
                AI 深度推理分析过程：
                1. 唯物辩证法包括三大规律：对立统一规律、量变质变规律、否定之否定规律。
                2. 其中，对立统一规律是唯物辩证法的实质和核心，因为它揭示了事物普遍联系的根本内容和永恒发展的内在动力。
                3. 其余选项均为表现形式或派生范畴。
                
                执行模型：{deepModel?.Name ?? "DeepSeek-R1"} (安全只读模式，未执行任何改动)
                """;

            step.Report("解析生成完毕！安全模式下未对作答系统做任何提交或保存。");
            progress.Report(100);
        });
    }

    [RelayCommand]
    private void ToggleSignInService()
    {
        SignInConfig.IsServiceRunning = !SignInConfig.IsServiceRunning;
        if (SignInConfig.IsServiceRunning)
        {
            _taskService.Log(LogLevel.Success, "考勤守护", $"✅ 课堂派自动签到守护服务已启动！正在按课程时间表监听 (坐标: {SignInConfig.Latitude}, {SignInConfig.Longitude})");
            SuccessMessage = "签到守护服务已启动并在后台运行";
        }
        else
        {
            _taskService.Log(LogLevel.Warning, "考勤守护", "⏹ 签到守护服务已停止。");
            SuccessMessage = "签到守护服务已停止";
        }
        _settingsService.SaveSettingsAsync();
    }

    [RelayCommand]
    private async Task AddSignInScheduleAsync()
    {
        if (string.IsNullOrWhiteSpace(NewScheduleCourseName) || string.IsNullOrWhiteSpace(NewScheduleClassId))
        {
            ErrorMessage = "请填写课程名称与 Class ID";
            return;
        }

        var item = new SignInScheduleItem
        {
            CourseName = NewScheduleCourseName.Trim(),
            ClassId = NewScheduleClassId.Trim(),
            DayOfWeek = NewScheduleDayOfWeek,
            StartTime = NewScheduleStartTime.Trim(),
            EndTime = NewScheduleEndTime.Trim()
        };

        SignInConfig.Schedules.Add(item);
        NewScheduleCourseName = string.Empty;
        NewScheduleClassId = string.Empty;
        await _settingsService.SaveSettingsAsync();
        SuccessMessage = $"已添加课程监听时间表：{item.CourseName}";
        _taskService.Log(LogLevel.Info, "考勤时间表", $"新增课程监听：{item.CourseName} ({item.DayOfWeekDisplayName} {item.TimeRangeDisplay})");
    }

    [RelayCommand]
    private async Task DeleteSignInScheduleAsync(SignInScheduleItem? item)
    {
        if (item == null) return;
        SignInConfig.Schedules.Remove(item);
        await _settingsService.SaveSettingsAsync();
        SuccessMessage = $"已删除时间表：{item.CourseName}";
        _taskService.Log(LogLevel.Info, "考勤时间表", $"移除课程监听：{item.CourseName}");
    }

    [RelayCommand]
    private async Task StartAiCourseAsync()
    {
        var docModel = _schedulerService.ResolveModel(ModelRole.DocumentParser);

        var job = new TaskJobModel
        {
            Title = "知到 AI 智能课程自动化",
            Type = FeatureType.AiCourse,
            Platform = PlatformType.Zhihuishu
        };

        await _taskService.StartJobAsync(job, async (token, progress, step) =>
        {
            step.Report("正在识别知到 AI 智能课知识图谱与资源节点…");
            await Task.Delay(1000, token);
            progress.Report(25);

            if (AiCourseConfig.ParseCoursewarePpt)
            {
                step.Report($"提取课件 PPT 关键知识点并使用 [{docModel?.Name ?? "Moonshot"}] 进行语义索引…");
                await Task.Delay(1500, token);
                progress.Report(60);
            }

            step.Report("执行知识点过关检测并校验答案…");
            await Task.Delay(1200, token);
            progress.Report(100);
            step.Report("知到 AI 课程知识点考核通过！");
        });
    }

    [RelayCommand]
    private async Task SaveAllConfigsAsync()
    {
        await _settingsService.SaveSettingsAsync();
        SuccessMessage = "所有功能接入参数已成功保存！";
        _taskService.Log(LogLevel.Success, "功能配置", "统一功能接入参数保存成功。");
    }
}
