using FuckRubbishClass;
using FuckRubbishClass.Core.Events;
using FuckRubbishClass.Core.Logging;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using FuckRubbishClass.ViewModels;
using FuckRubbishClass.Views;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using System.Windows.Markup;
using System.Xml.Linq;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        var app = new Application();
        System.Reflection.Assembly.Load("Wpf.Ui");
        System.Reflection.Assembly.Load("FuckRubbishClass");
        var appSource = XDocument.Load(Path.Combine(Directory.GetCurrentDirectory(), "src", "FuckRubbishClass", "App.xaml"));
        var dictionary = appSource.Descendants().First(e => e.Name.LocalName == "ResourceDictionary");
        foreach (var declaration in appSource.Root!.Attributes().Where(a => a.IsNamespaceDeclaration))
            dictionary.SetAttributeValue(declaration.Name, declaration.Value.StartsWith("clr-namespace:")
                ? declaration.Value + ";assembly=FuckRubbishClass" : declaration.Value);
        foreach (var element in dictionary.DescendantsAndSelf().Where(e => e.Name.NamespaceName.StartsWith("clr-namespace:")).ToList())
            element.Name = XName.Get(element.Name.LocalName, element.Name.NamespaceName + ";assembly=FuckRubbishClass");
        app.Resources = (ResourceDictionary)XamlReader.Parse(dictionary.ToString(), new ParserContext
        {
            BaseUri = new Uri("pack://application:,,,/FuckRubbishClass;component/App.xaml")
        });
        app.ShutdownMode = ShutdownMode.OnExplicitShutdown;
        var exit = 1;
        app.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle, new Action(async () =>
        {
            try
            {
                await VerifyAsync(args.Length > 0 ? Path.GetFullPath(args[0]) : Path.Combine(AppContext.BaseDirectory, "screenshots"));
                exit = 0;
            }
            catch (Exception ex) { Console.Error.WriteLine(ex); }
            finally { Dispatcher.CurrentDispatcher.BeginInvokeShutdown(DispatcherPriority.Send); }
        }));
        // Use production resources while keeping the startup window under test control.
        Dispatcher.Run();
        return exit;
    }

    private static async Task VerifyAsync(string output)
    {
        Directory.CreateDirectory(output);
        var bindings = new BindingErrors();
        PresentationTraceSources.DataBindingSource.Switch.Level = SourceLevels.Error;
        PresentationTraceSources.DataBindingSource.Listeners.Add(bindings);
        var main = new MainWindow();
        main.Close();
        Console.WriteLine("PASS: production window initializes before subscribing to navigation events");
        var data = Path.Combine(output, "data");
        var logger = new UiLogger();
        var events = new EventBus();
        var backend = new FixtureBackend();
        var settings = new SettingsService(data, logger, events);
        var accounts = new AccountService(backend, data, logger, events);
        var courses = new CourseService(accounts, backend, data, logger, events);
        var tasks = new TaskExecutionService(logger, events);
        var scheduler = new ModelSchedulerService(data, logger, events);
        var zhs = new AccountModel { Platform = PlatformType.Zhihuishu, DisplayName = "智慧树测试账号", IsPrimary = true };
        var cx = new AccountModel { Platform = PlatformType.Chaoxing, DisplayName = "学习通测试账号" };
        var ktp = new AccountModel { Platform = PlatformType.Ketangpai, DisplayName = "课堂派测试账号", Username = "fixture" };
        ktp.ExtraProperties["Password"] = "fixture-password";
        foreach (var account in new[] { zhs, cx, ktp }) accounts.Accounts.Add(account);
        accounts.ActiveAccount = zhs;
        courses.Courses.Add(Course(zhs, "现代科技与生活", "zhidao"));
        courses.Courses.Add(Course(zhs, "AI 学习方法", "zhidao_ai", true));
        courses.Courses.Add(Course(cx, "大学英语", "video"));
        var dashboard = new DashboardView { DataContext = new DashboardViewModel(accounts, courses, scheduler, tasks, events) };
        Render(new Border { Background = Brushes.WhiteSmoke, Padding = new Thickness(24), Child = dashboard }, Path.Combine(output, "dashboard.png"));
        Console.WriteLine("PASS: dashboard template loads and renders its activity feed");
        var vm = new FeaturesViewModel(settings, tasks, accounts, courses, backend);
        var view = new FeaturesView { DataContext = vm };
        var surface = new Border { Background = Brushes.WhiteSmoke, Padding = new Thickness(24), Child = view };
        var sections = new[] { "VideoSection", "HomeworkSection", "ExamSection", "AttendanceSection", "AiSection" };
        var tabs = new[] { "video", "homework", "exam", "signin", "aicourse" };
        for (var i = 0; i < tabs.Length; i++)
        {
            vm.ActiveFeatureTab = tabs[i];
            await Idle();
            for (var j = 0; j < sections.Length; j++)
                Check(((Border)view.FindName(sections[j])).Visibility == (i == j ? Visibility.Visible : Visibility.Collapsed), "Tab panels overlap");
            Check(vm.AvailableCourses.All(c => c.AccountId == vm.SelectedAccount?.Id), "Course selector crossed account boundaries");
            Render(surface, Path.Combine(output, tabs[i] + ".png"));
        }
        Console.WriteLine("PASS: five actual WPF panels load, switch exclusively and filter accounts/courses");

        vm.ActiveFeatureTab = "homework";
        Check(vm.AvailableCourses.All(c => !c.IsAiCourse) && vm.SelectedCourse?.IsAiCourse == false,
            "Homework selector kept an unsupported AI course after switching tabs");
        await vm.LoadHomeworksCommand.ExecuteAsync(null);
        Check(vm.Homeworks.Count == 1 && vm.SelectedHomework != null, "Homework list did not reach the selector");
        await vm.FetchHomeworkAnswersCommand.ExecuteAsync(null);
        Check(vm.HomeworkAnswers.Single().Answer.Contains("AC"), "Answer preview was not populated");
        await vm.HarvestHomeworkAnswersCommand.ExecuteAsync(null);
        Check(vm.HomeworkResult.Contains("1"), "Answer bank result was not displayed");
        await vm.RunSelectedHomeworkCommand.ExecuteAsync(null);
        await WaitUntil(() => tasks.Jobs.Count > 0 && tasks.FinishedJobs.Count == tasks.Jobs.Count);
        var homeworkRequest = backend.Calls.Last(c => c.Operation == "run_homework").Arguments;
        Check(!homeworkRequest.GetProperty("autoSubmit").GetBoolean(), "Draft protection was ignored");
        Render(surface, Path.Combine(output, "homework.png"));
        Console.WriteLine("PASS: homework selection, answer preview, answer bank import and draft protection");

        vm.ActiveFeatureTab = "exam";
        Check(vm.SelectedAccount?.Id == cx.Id && vm.SelectedHomework == null, "Platform switch kept an incompatible selection");
        await vm.LoadExamsCommand.ExecuteAsync(null);
        Check(vm.Exams.Count == 1 && vm.SelectedExam != null, "Exam selection is missing");
        await vm.RunSelectedExamCommand.ExecuteAsync(null);
        await WaitUntil(() => tasks.FinishedJobs.Count == tasks.Jobs.Count);
        Check(backend.Calls.Last(c => c.Operation == "run_exam").Arguments.GetProperty("examId").GetString() == "42", "Wrong exam was dispatched");
        await vm.RunExamReadonlyAnalysisCommand.ExecuteAsync(null);
        Check(vm.ExamAnalysisResult.Contains("本地测试解析"), "Readable explanation is missing");
        Render(surface, Path.Combine(output, "exam.png"));
        Console.WriteLine("PASS: exam list, selected exam dispatch and readable explanation");

        vm.ActiveFeatureTab = "signin";
        Check(vm.OperationMessage == vm.TargetHint, "Tab switch kept the previous operation status");
        await vm.RefreshServiceStatusCommand.ExecuteAsync(null);
        Check(vm.ServiceStatusText.Contains("依赖已安装"), "Service status is missing");
        await vm.ToggleSignInServiceCommand.ExecuteAsync(null);
        Check(settings.SignInConfig.IsServiceRunning, "UI service state did not reflect the backend");
        vm.NewScheduleCourseName = "课堂讨论";
        vm.NewScheduleClassId = "class-fixture";
        vm.NewScheduleDayOfWeek = 3;
        await vm.AddSignInScheduleCommand.ExecuteAsync(null);
        Check(settings.SignInConfig.Schedules.Single().DayOfWeek == 3, "Schedule weekday was lost");
        await vm.AddSignInScheduleCommand.ExecuteAsync(null);
        Check(!string.IsNullOrWhiteSpace(vm.ErrorMessage), "Invalid schedule did not show an error");
        vm.ClearMessages();
        vm.OperationMessage = "课堂派守护已启动，课程时间表已保存。";
        Render(surface, Path.Combine(output, "signin.png"));
        Console.WriteLine("PASS: backend service status, start/stop and schedule validation");

        var accountVm = new AccountsViewModel(accounts, tasks, backend, courses);
        var accountView = new AccountsView { DataContext = accountVm };
        accountVm.EditAccountCommand.Execute(ktp);
        accountVm.NewAccountDisplayName = "课堂派测试账号（已编辑）";
        await accountVm.ConfirmAddAccountCommand.ExecuteAsync(null);
        Check(accounts.Accounts.Count == 3 && ktp.ExtraProperties["Password"] == "fixture-password", "Account edit replaced the identity or cleared its password");
        Render(new Border { Background = Brushes.WhiteSmoke, Padding = new Thickness(24), Child = accountView }, Path.Combine(output, "accounts.png"));
        var hub = new CourseHubViewModel(courses, tasks, accounts, backend);
        var selectionNotification = false;
        hub.PropertyChanged += (_, e) => { if (e.PropertyName == nameof(hub.SelectedCount)) selectionNotification = true; };
        courses.Courses.First().IsSelected = true;
        Check(selectionNotification && hub.SelectedCount == 1, "Checkbox selection count did not update");
        Render(new Border { Background = Brushes.WhiteSmoke, Padding = new Thickness(24), Child = new CourseHubView { DataContext = hub } }, Path.Combine(output, "courses.png"));
        Console.WriteLine("PASS: account editor preserves identity/credentials and course checkbox count updates");

        backend.FailNext = true;
        vm.ActiveFeatureTab = "exam";
        await vm.LoadExamsCommand.ExecuteAsync(null);
        Check(vm.ErrorMessage == "测试连接失败" && !vm.IsBusy, "Backend error did not restore the UI");
        await Idle();
        Check(bindings.Errors.Count == 0, "WPF binding errors: " + string.Join(Environment.NewLine, bindings.Errors));
        Console.WriteLine("PASS: frontend error recovery and no WPF binding errors");

        var setup = new FixtureBackendSetup();
        var settingsVm = new SettingsViewModel(settings, tasks, setup);
        var settingsView = new SettingsView { DataContext = settingsVm };
        Check(settingsView.FindName("InstallBackendDependenciesButton") is Button,
            "Python backend dependency install button is missing");
        var setupPanel = (Border)settingsView.FindName("BackendSetupPanel");
        Render(new Border { Background = Brushes.WhiteSmoke, Padding = new Thickness(24), Child = settingsView },
            Path.Combine(output, "settings.png"));
        Check(setupPanel.Visibility == Visibility.Collapsed, "Backend setup panel showed before startup dependency check");
        await settingsVm.CheckPythonBackendDependenciesAsync();
        await Idle();
        Check(setupPanel.Visibility == Visibility.Visible, "Missing Python dependencies did not show the install panel");
        await settingsVm.InstallBackendDependenciesCommand.ExecuteAsync(null);
        await Idle();
        Check(setup.Called && settingsVm.SuccessMessage?.Contains("Python 后端依赖已安装") == true &&
            setupPanel.Visibility == Visibility.Collapsed,
            "Python backend dependency install command did not complete");

        var installedSetup = new FixtureBackendSetup(installed: true);
        var installedSettingsVm = new SettingsViewModel(settings, tasks, installedSetup);
        var installedSettingsView = new SettingsView { DataContext = installedSettingsVm };
        var installedSetupPanel = (Border)installedSettingsView.FindName("BackendSetupPanel");
        await installedSettingsVm.CheckPythonBackendDependenciesAsync();
        Render(new Border { Background = Brushes.WhiteSmoke, Padding = new Thickness(24), Child = installedSettingsView },
            Path.Combine(output, "settings-installed.png"));
        Check(installedSetupPanel.Visibility == Visibility.Collapsed,
            "Backend setup panel remained visible when dependencies were already installed at startup");
        Check(bindings.Errors.Count == 0, "WPF binding errors: " + string.Join(Environment.NewLine, bindings.Errors));
        Console.WriteLine("PASS: Python backend setup button is wired and reports completion");

        Console.WriteLine("Screenshots: " + output);
    }

    private static CourseModel Course(AccountModel account, string name, string kind, bool ai = false) => new()
    {
        AccountId = account.Id, AccountDisplayName = account.DisplayTitle, Platform = account.Platform,
        CourseId = Guid.NewGuid().ToString("N"), Name = name, IsAiCourse = ai,
        RawMetadata = JsonSerializer.SerializeToElement(new { kind, native_id = "1", class_id = "2", cpi = "3" })
    };
    private static void Check(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static async Task Idle() => await Application.Current.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ContextIdle);
    private static async Task WaitUntil(Func<bool> check)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!check()) await Task.Delay(10, timeout.Token);
    }
    private static void Render(FrameworkElement element, string path)
    {
        element.Measure(new Size(1160, 980));
        element.Arrange(new Rect(0, 0, 1160, 980));
        element.UpdateLayout();
        var bitmap = new RenderTargetBitmap(1160, 980, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(element);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var stream = File.Create(path);
        encoder.Save(stream);
    }
}

internal sealed class BindingErrors : TraceListener
{
    public List<string> Errors { get; } = new();
    public override void Write(string? message) { if (!string.IsNullOrWhiteSpace(message)) Errors.Add(message); }
    public override void WriteLine(string? message) => Write(message);
}

internal sealed class FixtureBackend : IPlatformBackendService
{
    public readonly List<(string Operation, JsonElement Arguments)> Calls = new();
    public bool FailNext;
    private bool _running;
    public Task<bool> CheckAvailableAsync() => Task.FromResult(true);
    public Task<JsonElement> InvokeAsync(AccountModel account, string operation, object? arguments = null,
        Action<JsonElement>? onProgress = null, CancellationToken token = default)
    {
        token.ThrowIfCancellationRequested();
        Calls.Add((operation, JsonSerializer.SerializeToElement(arguments)));
        if (FailNext) { FailNext = false; throw new BackendOperationException("测试连接失败"); }
        if (operation == "start_service") _running = true;
        if (operation == "stop_service") _running = false;
        return Task.FromResult(operation switch
        {
            "list_homeworks" => JsonSerializer.SerializeToElement(new { ok = true, homeworks = new[] { new { name = "第一章单元测试", chapterName = "课程基础", deadline = "本地测试数据", examId = "12" } } }),
            "fetch_homework_answers" => JsonSerializer.SerializeToElement(new { ok = true, result = new { items = new[] { new { question = "测试题面：请选择正确选项。", letters = "AC", error = "" } } } }),
            "harvest_homework_answers" => JsonSerializer.SerializeToElement(new { ok = true, result = new { saved = 1, skipped = 0, items = new[] { new { question = "测试题面：请选择正确选项。", letters = "AC", error = "" } } } }),
            "list_exams" => JsonSerializer.SerializeToElement(new { ok = true, exams = new[] { new { title = "课程阶段测验", courseName = "大学英语", statusText = "未开始", endTime = "本地测试数据", examId = "42", courseId = "1", classId = "2", cpi = "3" } } }),
            "explain_question" => JsonSerializer.SerializeToElement(new { ok = true, result = new { explanation = "本地测试解析：从题干给定条件出发，逐项核对概念和适用范围。", concepts = new[] { "概念辨析", "条件推理" }, steps = new[] { "提取题目条件", "对照选项，检查例外条件" } } }),
            "service_status" => JsonSerializer.SerializeToElement(new { ok = true, status = new { running = _running, runtimeAvailable = true, dependencies = true, configured = true } }),
            _ => JsonSerializer.SerializeToElement(new { ok = true, result = new { submitted = false } })
        });
    }
    public Task<IReadOnlyList<CourseModel>> GetCoursesAsync(AccountModel account, CancellationToken token = default) =>
        Task.FromResult<IReadOnlyList<CourseModel>>(Array.Empty<CourseModel>());
    public Task RunCourseAsync(AccountModel account, CourseModel course, FeatureType feature,
        IProgress<double> progress, IProgress<string> step, CancellationToken token) => Task.CompletedTask;
    public void ReleaseAccount(string accountId) { }
    public void Dispose() { }
}

internal sealed class FixtureBackendSetup : IBackendSetupService
{
    public bool Called { get; private set; }
    private bool _ready;

    public FixtureBackendSetup(bool installed = false) => _ready = installed;

    public Task<bool> ArePythonDependenciesInstalledAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(_ready);

    public Task InstallPythonDependenciesAsync(Action<string>? reportProgress = null,
        CancellationToken cancellationToken = default)
    {
        Called = true;
        _ready = true;
        reportProgress?.Invoke("测试安装已完成");
        return Task.CompletedTask;
    }
}

internal sealed class UiLogger : ILoggerService
{
    public ObservableCollection<LogEntry> LiveLogs { get; } = new();
    public void Log(LogLevel level, string category, string message, Exception? exception = null, object? payload = null) { }
    public void Debug(string category, string message) { }
    public void Info(string category, string message) { }
    public void Success(string category, string message) { }
    public void Warn(string category, string message, Exception? exception = null) { }
    public void Error(string category, string message, Exception? exception = null) { }
    public void Clear() { }
    public Task ExportLogsAsync(string path) => Task.CompletedTask;
    public string GetCurrentLogFilePath() => "";
}
