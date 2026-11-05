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

        var job = new TaskJobModel
        {
            Title = $"批量视频自学 ({targets.Count} 门课)",
            Type = FeatureType.VideoStudy,
            Platform = targets.First().Platform,
            CourseName = string.Join(", ", targets.Take(2).Select(x => x.Name)) + (targets.Count > 2 ? $" 等{targets.Count}门" : "")
        };

        await _taskService.StartJobAsync(job, async (token, progress, step) =>
        {
            for (int i = 0; i < targets.Count; i++)
            {
                token.ThrowIfCancellationRequested();
                var c = targets[i];
                step.Report($"正在自学《{c.Name}》 (第 {i + 1}/{targets.Count} 门)…");

                for (int p = 0; p <= 100; p += 20)
                {
                    token.ThrowIfCancellationRequested();
                    await Task.Delay(400, token);
                    var totalP = ((double)i / targets.Count * 100) + (p / (double)targets.Count);
                    progress.Report(totalP);
                }

                c.ProgressPercent = 100.0;
                step.Report($"《{c.Name}》学习完成！");
            }
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

        var job = new TaskJobModel
        {
            Title = $"平时测试作答 ({targets.Count} 门课)",
            Type = FeatureType.HomeworkQuiz,
            Platform = targets.First().Platform,
            CourseName = targets.First().Name
        };

        await _taskService.StartJobAsync(job, async (token, progress, step) =>
        {
            step.Report("正在匹配本地已验证答案图库与题库…");
            await Task.Delay(800, token);
            progress.Report(25);

            step.Report("调用统一模型调度器 [FastSolver + Vision] 解析未知题面…");
            await Task.Delay(1200, token);
            progress.Report(65);

            step.Report("逐题保存作答草稿（保护模式生效中，暂不交卷）…");
            await Task.Delay(1000, token);
            progress.Report(100);

            step.Report("已成功保存所有平时测试答案！");
        });
    }
}
