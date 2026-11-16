using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;
using System.ComponentModel;

namespace FuckRubbishClass.ViewModels;

public partial class CourseHubViewModel : ViewModelBase
{
    private readonly ICourseService _courseService;
    private readonly ITaskExecutionService _taskService;
    private readonly IAccountService _accountService;
    private readonly IPlatformBackendService _backend;
    private readonly HashSet<CourseModel> _observedCourses = new();

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

    public CourseHubViewModel(ICourseService courseService, ITaskExecutionService taskService, IAccountService accountService, IPlatformBackendService backend)
    {
        _courseService = courseService;
        _taskService = taskService;
        _accountService = accountService;
        _backend = backend;

        RefreshDisplayList();
        _courseService.Courses.CollectionChanged += (_, _) => RefreshDisplayList();
    }

    partial void OnSearchKeywordChanged(string value) => RefreshDisplayList();
    partial void OnSelectedPlatformFilterChanged(string value) => RefreshDisplayList();

    public void RefreshDisplayList()
    {
        foreach (var removed in _observedCourses.Where(c => !AllCourses.Contains(c)).ToList())
        {
            removed.PropertyChanged -= CoursePropertyChanged;
            _observedCourses.Remove(removed);
        }
        foreach (var course in AllCourses)
            if (_observedCourses.Add(course)) course.PropertyChanged += CoursePropertyChanged;
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

    private void CoursePropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(CourseModel.IsSelected)) OnPropertyChanged(nameof(SelectedCount));
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
        ClearMessages();
        IsBusy = true;
        BusyMessage = "正在从各平台同步课程…";
        var failures = new List<string>();
        try
        {
            foreach (var account in _accountService.Accounts.ToList())
            {
                try { await _courseService.RefreshCoursesForAccountAsync(account.Id); }
                catch (Exception ex)
                {
                    failures.Add($"{account.DisplayTitle}: {ex.Message}");
                    _taskService.Log(LogLevel.Error, "课程同步", failures.Last());
                }
            }
            RefreshDisplayList();
            if (failures.Count > 0) ErrorMessage = string.Join(Environment.NewLine, failures);
            else SuccessMessage = $"课程同步完成，共 {AllCourses.Count} 门课程";
        }
        finally { IsBusy = false; }
    }

    [RelayCommand]
    private Task RunBatchVideoStudyAsync() => QueueBatchAsync(FeatureType.VideoStudy);

    [RelayCommand]
    private Task RunBatchHomeworkAsync() => QueueBatchAsync(FeatureType.HomeworkQuiz);

    private async Task QueueBatchAsync(FeatureType feature)
    {
        ClearMessages();
        var targets = DisplayCourses.Where(c => c.IsSelected).ToList();
        if (targets.Count == 0 && SelectedCourse != null) targets.Add(SelectedCourse);
        if (targets.Count == 0) { ErrorMessage = "请先选择至少一门课程"; return; }
        if (targets.Any(c => c.Platform == PlatformType.Ketangpai ||
            (feature == FeatureType.HomeworkQuiz && c.Platform != PlatformType.Zhihuishu)))
        {
            ErrorMessage = feature == FeatureType.HomeworkQuiz
                ? "平时测试请仅选择智慧树课程。" : "课堂派请使用考勤守护功能。";
            return;
        }
        var work = targets.Select(course => new
        {
            Course = course,
            Account = _accountService.Accounts.FirstOrDefault(a => a.Id == course.AccountId),
            Job = new TaskJobModel
            {
                Title = $"{(feature == FeatureType.HomeworkQuiz ? "平时测试" : "视频自学")}: {course.Name}",
                Type = feature, Platform = course.Platform, AccountId = course.AccountId,
                AccountName = course.AccountDisplayName, CourseId = course.CourseId, CourseName = course.Name
            }
        }).ToDictionary(item => item.Job.Id);
        if (work.Values.Any(item => item.Account == null))
        {
            ErrorMessage = "课程缺少所属账号，请重新同步课程。";
            return;
        }
        await _taskService.EnqueueBatchJobsAsync(work.Values.Select(item => item.Job),
            (job, token, progress, step) =>
            {
                var item = work[job.Id];
                return _backend.RunCourseAsync(item.Account!, item.Course, feature, progress, step, token);
            });
        SuccessMessage = $"已加入 {work.Count} 个任务，请在任务监控查看进度。";
    }
}
