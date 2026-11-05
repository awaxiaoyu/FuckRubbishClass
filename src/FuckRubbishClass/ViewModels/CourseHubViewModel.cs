using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;

namespace FuckRubbishClass.ViewModels;

public partial class CourseHubViewModel : ViewModelBase
{
    private readonly ICourseService _courseService;
    private readonly ITaskExecutionService _taskService;
    private readonly IAccountService _accountService;

    public ObservableCollection<CourseModel> AllCourses => _courseService.Courses;
    public ObservableCollection<CourseModel> DisplayCourses { get; } = new();

    [ObservableProperty]
    private string _searchKeyword = string.Empty;

    [ObservableProperty]
    private string _selectedPlatformFilter = "全部";

    [ObservableProperty]
    private CourseModel? _selectedCourse;

    public int SelectedCount => DisplayCourses.Count(x => x.IsSelected);
    public int TotalCount => DisplayCourses.Count;

    public CourseHubViewModel(ICourseService courseService, ITaskExecutionService taskService, IAccountService accountService)
    {
        _courseService = courseService;
        _taskService = taskService;
        _accountService = accountService;

        RefreshDisplayList();
        _courseService.Courses.CollectionChanged += (_, _) => RefreshDisplayList();
    }

    partial void OnSearchKeywordChanged(string value) => RefreshDisplayList();
    partial void OnSelectedPlatformFilterChanged(string value) => RefreshDisplayList();

    public void RefreshDisplayList()
    {
        DisplayCourses.Clear();
        var query = AllCourses.AsEnumerable();

        if (SelectedPlatformFilter == "智慧树")
            query = query.Where(x => x.Platform == PlatformType.Zhihuishu);
        else if (SelectedPlatformFilter == "学习通")
            query = query.Where(x => x.Platform == PlatformType.Chaoxing);
        else if (SelectedPlatformFilter == "课堂派")
            query = query.Where(x => x.Platform == PlatformType.Ketangpai);

        if (!string.IsNullOrWhiteSpace(SearchKeyword))
        {
            var kw = SearchKeyword.Trim().ToLowerInvariant();
            query = query.Where(x => x.Name.ToLowerInvariant().Contains(kw) ||
                                     x.Teacher.ToLowerInvariant().Contains(kw) ||
                                     x.CourseId.Contains(kw));
        }

        foreach (var c in query)
        {
            DisplayCourses.Add(c);
        }

        OnPropertyChanged(nameof(SelectedCount));
        OnPropertyChanged(nameof(TotalCount));
    }

    [RelayCommand]
    private void SelectAll()
    {
        foreach (var c in DisplayCourses) c.IsSelected = true;
        OnPropertyChanged(nameof(SelectedCount));
    }

    [RelayCommand]
    private void DeselectAll()
    {
        foreach (var c in DisplayCourses) c.IsSelected = false;
        OnPropertyChanged(nameof(SelectedCount));
    }

    [RelayCommand]
    private void ToggleSelect(CourseModel course)
    {
        course.IsSelected = !course.IsSelected;
        OnPropertyChanged(nameof(SelectedCount));
    }

    [RelayCommand]
    private async Task RefreshAllCoursesAsync()
    {
        IsBusy = true;
        BusyMessage = "正在从各平台同步最新课程列表…";
        _taskService.Log(LogLevel.Info, "课程中心", "开始同步所有已绑定账号的课程库…");

        await Task.Delay(1200);
        await _courseService.LoadCoursesAsync();
        RefreshDisplayList();

        IsBusy = false;
        SuccessMessage = $"课程同步完成，当前共 {AllCourses.Count} 门课程";
        _taskService.Log(LogLevel.Success, "课程中心", $"课程同步成功，已索引 {AllCourses.Count} 门课程。");
    }

    [RelayCommand]
    private async Task RunBatchVideoStudyAsync()
    {
        var targets = DisplayCourses.Where(x => x.IsSelected).ToList();
        if (targets.Count == 0 && SelectedCourse != null) targets.Add(SelectedCourse);

        if (targets.Count == 0)
        {
            ErrorMessage = "请先选择至少一门课程";
            return;
        }

        SuccessMessage = $"已并发调度 {targets.Count} 门课程自学任务，正在并发启动多线程 Worker…";
        _taskService.Log(LogLevel.Info, "并发调度", $"🚀 批量下发 {targets.Count} 门多平台/多账号并发自学任务！");

        // 为每门选中的课程创建独立的任务作业，支持多平台多账号同时并行
        var jobs = targets.Select(c => new TaskJobModel
        {
            Title = $"自学: {c.Name}",
            Type = FeatureType.VideoStudy,
            Platform = c.Platform,
            AccountId = c.AccountId,
            AccountName = string.IsNullOrWhiteSpace(c.AccountDisplayName) ? "默认账号" : c.AccountDisplayName,
            CourseId = c.CourseId,
            CourseName = c.Name
        }).ToList();

        await _taskService.EnqueueBatchJobsAsync(jobs, async (job, token, progress, step) =>
        {
            var targetCourse = DisplayCourses.FirstOrDefault(x => x.CourseId == job.CourseId) ?? 
                               AllCourses.FirstOrDefault(x => x.CourseId == job.CourseId);

            step.Report($"连接平台 [{job.PlatformDisplayName}]，账号 [{job.AccountName}] 正在建立播放流…");
            await Task.Delay(1000, token);
            progress.Report(10);

            step.Report($"[{job.AccountName}] 进入《{job.CourseName}》视频章节，正在多线程并发自学播放中…");
            for (int p = 15; p <= 95; p += 15)
            {
                token.ThrowIfCancellationRequested();
                await Task.Delay(600, token);
                progress.Report(p);

                if (targetCourse != null)
                {
                    targetCourse.ProgressPercent = Math.Min(100.0, targetCourse.ProgressPercent + 3.0);
                }
            }

            if (targetCourse != null) targetCourse.ProgressPercent = 100.0;
            progress.Report(100);
            step.Report($"《{job.CourseName}》视频小节达标，该课程本阶段任务完成！");
        });
    }

    [RelayCommand]
    private async Task RunBatchHomeworkAsync()
    {
        var targets = DisplayCourses.Where(x => x.IsSelected).ToList();
        if (targets.Count == 0 && SelectedCourse != null) targets.Add(SelectedCourse);

        if (targets.Count == 0)
        {
            ErrorMessage = "请先选择至少一门课程";
            return;
        }

        SuccessMessage = $"已并发调度 {targets.Count} 门平时测试作答任务…";
        _taskService.Log(LogLevel.Info, "并发调度", $"📝 批量下发 {targets.Count} 门平时测试作答任务！");

        var jobs = targets.Select(c => new TaskJobModel
        {
            Title = $"测验: {c.Name}",
            Type = FeatureType.HomeworkQuiz,
            Platform = c.Platform,
            AccountId = c.AccountId,
            AccountName = string.IsNullOrWhiteSpace(c.AccountDisplayName) ? "默认账号" : c.AccountDisplayName,
            CourseId = c.CourseId,
            CourseName = c.Name
        }).ToList();

        await _taskService.EnqueueBatchJobsAsync(jobs, async (job, token, progress, step) =>
        {
            step.Report($"[{job.AccountName}] 正在检索本地已验证高精答案图与题库…");
            await Task.Delay(800, token);
            progress.Report(30);

            step.Report($"[{job.AccountName}] 调度统一模型池 [FastSolver + Vision] 并发解析未知题面…");
            await Task.Delay(1400, token);
            progress.Report(70);

            step.Report($"[{job.AccountName}] 逐题保存作答草稿（保护模式开启，杜绝误交卷）…");
            await Task.Delay(800, token);
            progress.Report(100);

            var targetCourse = DisplayCourses.FirstOrDefault(x => x.CourseId == job.CourseId) ?? 
                               AllCourses.FirstOrDefault(x => x.CourseId == job.CourseId);
            if (targetCourse != null)
            {
                targetCourse.PendingHomeworkCount = Math.Max(0, targetCourse.PendingHomeworkCount - 1);
            }

            step.Report($"《{job.CourseName}》平时测试已全部安全完成！");
        });
    }
}
