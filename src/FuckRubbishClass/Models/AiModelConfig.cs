using CommunityToolkit.Mvvm.ComponentModel;
using System.Text.Json.Serialization;

namespace FuckRubbishClass.Models;

public partial class AiModelConfig : ObservableObject
{
    [ObservableProperty]
    private string _id = Guid.NewGuid().ToString("N");

    [ObservableProperty]
    private string _name = "默认模型";

    [ObservableProperty]
    private ModelProviderType _provider = ModelProviderType.DeepSeek;

    [ObservableProperty]
    private string _baseUrl = "https://api.deepseek.com/v1";

    [ObservableProperty]
    private string _apiKey = string.Empty;

    [ObservableProperty]
    private string _modelId = "deepseek-chat";

    [ObservableProperty]
    private bool _supportsVision;

    [ObservableProperty]
    private bool _supportsStream = true;

    [ObservableProperty]
    private bool _supportsDocument;

    [ObservableProperty]
    private double _temperature = 0.1;

    [ObservableProperty]
    private int _maxTokens = 2048;

    [ObservableProperty]
    private int _timeoutSeconds = 60;

    [ObservableProperty]
    private bool _isEnabled = true;

    [ObservableProperty]
    private bool _isHealthy = true;

    [ObservableProperty]
    private long _latencyMs = -1;

    [ObservableProperty]
    private string _statusText = "未测试";

    [ObservableProperty]
    private int _totalRequests;

    [ObservableProperty]
    private int _successfulRequests;

    [JsonIgnore]
    public string LatencyDisplay => LatencyMs >= 0 ? $"{LatencyMs} ms" : "—";

    [JsonIgnore]
    public string ProviderDisplayName => Provider switch
    {
        ModelProviderType.DeepSeek => "DeepSeek",
        ModelProviderType.OpenAI => "OpenAI",
        ModelProviderType.Moonshot => "Moonshot / Kimi",
        ModelProviderType.SiliconFlow => "硅基流动 SiliconFlow",
        ModelProviderType.Claude => "Anthropic Claude",
        ModelProviderType.Qwen => "通义千问 Qwen",
        ModelProviderType.Ollama => "本地 Ollama",
        _ => "自定义 OpenAI 兼容 API"
    };

    [JsonIgnore]
    public string ProviderBadgeColor => Provider switch
    {
        ModelProviderType.DeepSeek => "#0066FF",
        ModelProviderType.OpenAI => "#10A37F",
        ModelProviderType.Moonshot => "#7038FF",
        ModelProviderType.SiliconFlow => "#8425F8",
        ModelProviderType.Claude => "#D97706",
        ModelProviderType.Qwen => "#FF5A1F",
        ModelProviderType.Ollama => "#2563EB",
        _ => "#64748B"
    };
}
