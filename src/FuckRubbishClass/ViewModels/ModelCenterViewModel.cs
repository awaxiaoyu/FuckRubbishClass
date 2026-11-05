using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using FuckRubbishClass.Models;
using FuckRubbishClass.Services;
using System.Collections.ObjectModel;

namespace FuckRubbishClass.ViewModels;

public partial class ModelCenterViewModel : ViewModelBase
{
    private readonly IModelSchedulerService _schedulerService;
    private readonly ITaskExecutionService _taskService;

    public ObservableCollection<AiModelConfig> Models => _schedulerService.Models;
    public ModelSchedulerConfig Scheduler => _schedulerService.SchedulerConfig;

    [ObservableProperty]
    private AiModelConfig? _selectedModel;

    [ObservableProperty]
    private bool _isEditingModel;

    [ObservableProperty]
    private bool _isNewModel;

    // Model editor fields
    [ObservableProperty]
    private string _editName = string.Empty;

    [ObservableProperty]
    private ModelProviderType _editProvider = ModelProviderType.DeepSeek;

    [ObservableProperty]
    private string _editBaseUrl = string.Empty;

    [ObservableProperty]
    private string _editApiKey = string.Empty;

    [ObservableProperty]
    private string _editModelId = string.Empty;

    [ObservableProperty]
    private bool _editSupportsVision;

    [ObservableProperty]
    private bool _editSupportsStream = true;

    [ObservableProperty]
    private bool _editSupportsDocument;

    [ObservableProperty]
    private double _editTemperature = 0.1;

    [ObservableProperty]
    private int _editMaxTokens = 2048;

    [ObservableProperty]
    private int _editTimeoutSeconds = 60;

    public ModelCenterViewModel(IModelSchedulerService schedulerService, ITaskExecutionService taskService)
    {
        _schedulerService = schedulerService;
        _taskService = taskService;
        SelectedModel = _schedulerService.Models.FirstOrDefault();
    }

    [RelayCommand]
    private void StartAddModel()
    {
        IsNewModel = true;
        EditName = "新自定义模型";
        EditProvider = ModelProviderType.DeepSeek;
        EditBaseUrl = "https://api.deepseek.com/v1";
        EditApiKey = string.Empty;
        EditModelId = "deepseek-chat";
        EditSupportsVision = false;
        EditSupportsStream = true;
        EditSupportsDocument = false;
        EditTemperature = 0.1;
        EditMaxTokens = 2048;
        EditTimeoutSeconds = 60;
        IsEditingModel = true;
    }

    [RelayCommand]
    private void StartEditModel(AiModelConfig? model)
    {
        var target = model ?? SelectedModel;
        if (target == null) return;

        IsNewModel = false;
        SelectedModel = target;
        EditName = target.Name;
        EditProvider = target.Provider;
        EditBaseUrl = target.BaseUrl;
        EditApiKey = target.ApiKey;
        EditModelId = target.ModelId;
        EditSupportsVision = target.SupportsVision;
        EditSupportsStream = target.SupportsStream;
        EditSupportsDocument = target.SupportsDocument;
        EditTemperature = target.Temperature;
        EditMaxTokens = target.MaxTokens;
        EditTimeoutSeconds = target.TimeoutSeconds;
        IsEditingModel = true;
    }

    [RelayCommand]
    private void CancelEditModel()
    {
        IsEditingModel = false;
    }

    [RelayCommand]
    private async Task SaveModelEditAsync()
    {
        if (string.IsNullOrWhiteSpace(EditName) || string.IsNullOrWhiteSpace(EditBaseUrl) || string.IsNullOrWhiteSpace(EditModelId))
        {
            ErrorMessage = "模型名称、Base URL 和模型 ID 不能为空";
            return;
        }

        if (IsNewModel)
        {
            var newModel = new AiModelConfig
            {
                Name = EditName.Trim(),
                Provider = EditProvider,
                BaseUrl = EditBaseUrl.Trim(),
                ApiKey = EditApiKey.Trim(),
                ModelId = EditModelId.Trim(),
                SupportsVision = EditSupportsVision,
                SupportsStream = EditSupportsStream,
                SupportsDocument = EditSupportsDocument,
                Temperature = EditTemperature,
                MaxTokens = EditMaxTokens,
                TimeoutSeconds = EditTimeoutSeconds,
                StatusText = "未测试"
            };
            await _schedulerService.AddModelAsync(newModel);
            SelectedModel = newModel;
            SuccessMessage = $"已添加新模型：{newModel.Name}";
            _taskService.Log(LogLevel.Success, "模型配置", $"已添加模型：{newModel.Name} ({newModel.ModelId})");
        }
        else if (SelectedModel != null)
        {
            SelectedModel.Name = EditName.Trim();
            SelectedModel.Provider = EditProvider;
            SelectedModel.BaseUrl = EditBaseUrl.Trim();
            SelectedModel.ApiKey = EditApiKey.Trim();
            SelectedModel.ModelId = EditModelId.Trim();
            SelectedModel.SupportsVision = EditSupportsVision;
            SelectedModel.SupportsStream = EditSupportsStream;
            SelectedModel.SupportsDocument = EditSupportsDocument;
            SelectedModel.Temperature = EditTemperature;
            SelectedModel.MaxTokens = EditMaxTokens;
            SelectedModel.TimeoutSeconds = EditTimeoutSeconds;
            await _schedulerService.SaveConfigAsync();
            SuccessMessage = $"已更新模型配置：{SelectedModel.Name}";
            _taskService.Log(LogLevel.Info, "模型配置", $"更新模型配置：{SelectedModel.Name}");
        }

        IsEditingModel = false;
    }

    [RelayCommand]
    private async Task DeleteModelAsync(AiModelConfig? model)
    {
        var target = model ?? SelectedModel;
        if (target == null) return;

        var name = target.Name;
        await _schedulerService.DeleteModelAsync(target.Id);
        SelectedModel = Models.FirstOrDefault();
        SuccessMessage = $"已删除模型：{name}";
        _taskService.Log(LogLevel.Info, "模型配置", $"删除模型：{name}");
    }

    [RelayCommand]
    private async Task TestSelectedModelAsync(AiModelConfig? model)
    {
        var target = model ?? SelectedModel;
        if (target == null) return;

        IsBusy = true;
        BusyMessage = $"正在测试 {target.Name} 连通性…";
        _taskService.Log(LogLevel.Info, "模型测试", $"正在测试 [{target.Name}] {target.BaseUrl}…");

        var ok = await _schedulerService.TestModelConnectionAsync(target.Id);
        IsBusy = false;

        if (ok)
        {
            SuccessMessage = $"{target.Name} 连接成功！延迟：{target.LatencyMs}ms";
            _taskService.Log(LogLevel.Success, "模型测试", $"✅ [{target.Name}] 连通测试通过，响应耗时 {target.LatencyMs}ms");
        }
        else
        {
            ErrorMessage = $"{target.Name} 连接失败：{target.StatusText}";
            _taskService.Log(LogLevel.Error, "模型测试", $"❌ [{target.Name}] 连通失败：{target.StatusText}");
        }
    }

    [RelayCommand]
    private async Task TestAllModelsAsync()
    {
        IsBusy = true;
        BusyMessage = "正在并发测试所有模型池节点的连通性…";
        _taskService.Log(LogLevel.Info, "模型调度", "开始全量模型连通性与测速巡检…");

        var tasks = Models.Select(m => _schedulerService.TestModelConnectionAsync(m.Id)).ToList();
        await Task.WhenAll(tasks);

        IsBusy = false;
        var onlineCount = Models.Count(x => x.IsHealthy);
        SuccessMessage = $"测试完成：{onlineCount}/{Models.Count} 个模型节点可用";
        _taskService.Log(LogLevel.Success, "模型调度", $"巡检结束，可用节点：{onlineCount}/{Models.Count}");
    }

    [RelayCommand]
    private async Task SaveSchedulerSettingsAsync()
    {
        await _schedulerService.SaveConfigAsync();
        SuccessMessage = "模型分工与调度策略已成功保存！";
        _taskService.Log(LogLevel.Success, "模型调度", "模型分工角色与降级容灾调度策略已保存。");
    }

    [RelayCommand]
    private void ApplyPreset(string presetName)
    {
        switch (presetName)
        {
            case "DeepSeek":
                EditName = "DeepSeek-V3 快速推理";
                EditProvider = ModelProviderType.DeepSeek;
                EditBaseUrl = "https://api.deepseek.com/v1";
                EditModelId = "deepseek-chat";
                EditSupportsVision = false;
                break;
            case "DeepSeekR1":
                EditName = "DeepSeek-R1 深度思考";
                EditProvider = ModelProviderType.DeepSeek;
                EditBaseUrl = "https://api.deepseek.com/v1";
                EditModelId = "deepseek-reasoner";
                EditSupportsVision = false;
                EditTemperature = 0.6;
                break;
            case "GPT4o":
                EditName = "OpenAI GPT-4o 视觉解析";
                EditProvider = ModelProviderType.OpenAI;
                EditBaseUrl = "https://api.openai.com/v1";
                EditModelId = "gpt-4o";
                EditSupportsVision = true;
                break;
            case "Moonshot":
                EditName = "Moonshot 课件文档";
                EditProvider = ModelProviderType.Moonshot;
                EditBaseUrl = "https://api.moonshot.cn/v1";
                EditModelId = "moonshot-v1-32k";
                EditSupportsDocument = true;
                break;
            case "SiliconFlow":
                EditName = "硅基流动 Qwen-2.5-72B";
                EditProvider = ModelProviderType.SiliconFlow;
                EditBaseUrl = "https://api.siliconflow.cn/v1";
                EditModelId = "Qwen/Qwen2.5-72B-Instruct";
                EditSupportsVision = false;
                break;
            case "Ollama":
                EditName = "本地 Ollama 本地模型";
                EditProvider = ModelProviderType.Ollama;
                EditBaseUrl = "http://localhost:11434/v1";
                EditModelId = "qwen2.5:7b";
                EditApiKey = "ollama";
                EditSupportsVision = false;
                break;
        }
    }
}
