using FuckRubbishClass.Models;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace FuckRubbishClass.Services;

public interface IModelSchedulerService
{
    ObservableCollection<AiModelConfig> Models { get; }
    ModelSchedulerConfig SchedulerConfig { get; }
    Task LoadConfigAsync();
    Task SaveConfigAsync();
    Task AddModelAsync(AiModelConfig model);
    Task DeleteModelAsync(string modelId);
    Task<bool> TestModelConnectionAsync(string modelId);
    AiModelConfig? ResolveModel(ModelRole role, bool isFallback = false);
}

public class ModelSchedulerService : IModelSchedulerService
{
    private readonly string _filePath;
    private readonly HttpClient _httpClient = new() { Timeout = TimeSpan.FromSeconds(15) };

    public ObservableCollection<AiModelConfig> Models { get; } = new();
    public ModelSchedulerConfig SchedulerConfig { get; } = new();

    public ModelSchedulerService(string? dataDir = null)
    {
        var dir = dataDir ?? Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "data");
        Directory.CreateDirectory(dir);
        _filePath = Path.Combine(dir, "models_scheduler.json");
    }

    public async Task LoadConfigAsync()
    {
        try
        {
            if (File.Exists(_filePath))
            {
                var json = await File.ReadAllTextAsync(_filePath);
                using var doc = JsonDocument.Parse(json);
                if (doc.RootElement.TryGetProperty("models", out var modelsElem))
                {
                    var loadedModels = JsonSerializer.Deserialize<List<AiModelConfig>>(modelsElem.GetRawText());
                    if (loadedModels != null && loadedModels.Count > 0)
                    {
                        Models.Clear();
                        foreach (var m in loadedModels) Models.Add(m);
                    }
                }
                if (doc.RootElement.TryGetProperty("scheduler", out var schedElem))
                {
                    var loadedSched = JsonSerializer.Deserialize<ModelSchedulerConfig>(schedElem.GetRawText());
                    if (loadedSched != null)
                    {
                        SchedulerConfig.FastSolverModelId = loadedSched.FastSolverModelId;
                        SchedulerConfig.FastSolverFallbackModelId = loadedSched.FastSolverFallbackModelId;
                        SchedulerConfig.VisionModelId = loadedSched.VisionModelId;
                        SchedulerConfig.VisionFallbackModelId = loadedSched.VisionFallbackModelId;
                        SchedulerConfig.DeepReasoningModelId = loadedSched.DeepReasoningModelId;
                        SchedulerConfig.DeepReasoningFallbackModelId = loadedSched.DeepReasoningFallbackModelId;
                        SchedulerConfig.DocumentParserModelId = loadedSched.DocumentParserModelId;
                        SchedulerConfig.DocumentParserFallbackModelId = loadedSched.DocumentParserFallbackModelId;
                        SchedulerConfig.AutoFallbackOnFailure = loadedSched.AutoFallbackOnFailure;
                        SchedulerConfig.AutoUpgradeToVisionOnImages = loadedSched.AutoUpgradeToVisionOnImages;
                        SchedulerConfig.MaxRetryAttempts = loadedSched.MaxRetryAttempts;
                        SchedulerConfig.StreamResponse = loadedSched.StreamResponse;
                        SchedulerConfig.LogAiReasoningChain = loadedSched.LogAiReasoningChain;
                        return;
                    }
                }
            }
        }
        catch
        {
            // Fallback to presets
        }

        SeedDefaultModelsAndScheduler();
        await SaveConfigAsync();
    }

    private void SeedDefaultModelsAndScheduler()
    {
        Models.Clear();

        var deepseekChat = new AiModelConfig
        {
            Id = "m_ds_chat",
            Name = "DeepSeek-V3 极速答题",
            Provider = ModelProviderType.DeepSeek,
            BaseUrl = "https://api.deepseek.com/v1",
            ApiKey = "",
            ModelId = "deepseek-chat",
            SupportsVision = false,
            SupportsStream = true,
            Temperature = 0.1,
            MaxTokens = 2048,
            TimeoutSeconds = 45,
            LatencyMs = 238,
            StatusText = "就绪 (预设)"
        };

        var deepseekR1 = new AiModelConfig
        {
            Id = "m_ds_r1",
            Name = "DeepSeek-R1 深度思考推理",
            Provider = ModelProviderType.DeepSeek,
            BaseUrl = "https://api.deepseek.com/v1",
            ApiKey = "",
            ModelId = "deepseek-reasoner",
            SupportsVision = false,
            SupportsStream = true,
            Temperature = 0.6,
            MaxTokens = 4096,
            TimeoutSeconds = 90,
            LatencyMs = 412,
            StatusText = "就绪 (预设)"
        };

        var gpt4o = new AiModelConfig
        {
            Id = "m_gpt4o",
            Name = "OpenAI GPT-4o 多模态视觉",
            Provider = ModelProviderType.OpenAI,
            BaseUrl = "https://api.openai.com/v1",
            ApiKey = "",
            ModelId = "gpt-4o",
            SupportsVision = true,
            SupportsStream = true,
            Temperature = 0.1,
            MaxTokens = 2048,
            TimeoutSeconds = 60,
            LatencyMs = 380,
            StatusText = "就绪 (预设)"
        };

        var moonshot = new AiModelConfig
        {
            Id = "m_moonshot",
            Name = "Moonshot / Kimi 课件解析",
            Provider = ModelProviderType.Moonshot,
            BaseUrl = "https://api.moonshot.cn/v1",
            ApiKey = "",
            ModelId = "moonshot-v1-32k",
            SupportsVision = false,
            SupportsDocument = true,
            SupportsStream = true,
            Temperature = 0.2,
            MaxTokens = 4096,
            TimeoutSeconds = 90,
            LatencyMs = 310,
            StatusText = "就绪 (预设)"
        };

        var siliconflow = new AiModelConfig
        {
            Id = "m_siliconflow",
            Name = "硅基流动 Qwen-2.5 备用快速源",
            Provider = ModelProviderType.SiliconFlow,
            BaseUrl = "https://api.siliconflow.cn/v1",
            ApiKey = "",
            ModelId = "Qwen/Qwen2.5-72B-Instruct",
            SupportsVision = false,
            SupportsStream = true,
            Temperature = 0.1,
            MaxTokens = 2048,
            TimeoutSeconds = 45,
            LatencyMs = 195,
            StatusText = "就绪 (预设)"
        };

        var localOllama = new AiModelConfig
        {
            Id = "m_ollama",
            Name = "本地 Ollama (完全离线)",
            Provider = ModelProviderType.Ollama,
            BaseUrl = "http://localhost:11434/v1",
            ApiKey = "ollama",
            ModelId = "qwen2.5:7b",
            SupportsVision = false,
            SupportsStream = true,
            Temperature = 0.1,
            MaxTokens = 2048,
            TimeoutSeconds = 60,
            LatencyMs = -1,
            StatusText = "本地未启动"
        };

        Models.Add(deepseekChat);
        Models.Add(deepseekR1);
        Models.Add(gpt4o);
        Models.Add(moonshot);
        Models.Add(siliconflow);
        Models.Add(localOllama);

        // Bind Roles
        SchedulerConfig.FastSolverModelId = deepseekChat.Id;
        SchedulerConfig.FastSolverFallbackModelId = siliconflow.Id;

        SchedulerConfig.VisionModelId = gpt4o.Id;
        SchedulerConfig.VisionFallbackModelId = deepseekChat.Id;

        SchedulerConfig.DeepReasoningModelId = deepseekR1.Id;
        SchedulerConfig.DeepReasoningFallbackModelId = deepseekChat.Id;

        SchedulerConfig.DocumentParserModelId = moonshot.Id;
        SchedulerConfig.DocumentParserFallbackModelId = gpt4o.Id;
    }

    public async Task SaveConfigAsync()
    {
        try
        {
            var data = new
            {
                models = Models.ToList(),
                scheduler = SchedulerConfig
            };
            var json = JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true });
            await File.WriteAllTextAsync(_filePath, json);
        }
        catch
        {
            // Ignore write errors in restricted envs
        }
    }

    public async Task AddModelAsync(AiModelConfig model)
    {
        Models.Add(model);
        await SaveConfigAsync();
    }

    public async Task DeleteModelAsync(string modelId)
    {
        var target = Models.FirstOrDefault(x => x.Id == modelId);
        if (target != null)
        {
            Models.Remove(target);
            if (SchedulerConfig.FastSolverModelId == modelId) SchedulerConfig.FastSolverModelId = Models.FirstOrDefault()?.Id ?? "";
            if (SchedulerConfig.VisionModelId == modelId) SchedulerConfig.VisionModelId = Models.FirstOrDefault(x => x.SupportsVision)?.Id ?? "";
            if (SchedulerConfig.DeepReasoningModelId == modelId) SchedulerConfig.DeepReasoningModelId = Models.FirstOrDefault()?.Id ?? "";
            if (SchedulerConfig.DocumentParserModelId == modelId) SchedulerConfig.DocumentParserModelId = Models.FirstOrDefault(x => x.SupportsDocument)?.Id ?? "";
            await SaveConfigAsync();
        }
    }

    public async Task<bool> TestModelConnectionAsync(string modelId)
    {
        var model = Models.FirstOrDefault(x => x.Id == modelId);
        if (model == null) return false;

        model.StatusText = "正在连通性测试…";
        var sw = Stopwatch.StartNew();

        try
        {
            var url = model.BaseUrl.TrimEnd('/');
            if (!url.EndsWith("/models") && !url.EndsWith("/chat/completions"))
            {
                url = $"{url}/models";
            }

            using var req = new HttpRequestMessage(HttpMethod.Get, url);
            if (!string.IsNullOrWhiteSpace(model.ApiKey))
            {
                req.Headers.TryAddWithoutValidation("Authorization", $"Bearer {model.ApiKey.Trim()}");
            }

            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(model.TimeoutSeconds > 0 ? Math.Min(model.TimeoutSeconds, 10) : 10));
            var resp = await _httpClient.SendAsync(req, cts.Token);
            sw.Stop();

            model.LatencyMs = sw.ElapsedMilliseconds;
            if (resp.IsSuccessStatusCode)
            {
                model.IsHealthy = true;
                model.StatusText = $"在线 ({model.LatencyMs}ms)";
                await SaveConfigAsync();
                return true;
            }
            else
            {
                model.IsHealthy = false;
                model.StatusText = $"HTTP {(int)resp.StatusCode} {resp.ReasonPhrase}";
                await SaveConfigAsync();
                return false;
            }
        }
        catch (Exception ex)
        {
            sw.Stop();
            model.LatencyMs = sw.ElapsedMilliseconds;
            model.IsHealthy = false;
            model.StatusText = $"连接失败: {ex.Message}";
            await SaveConfigAsync();
            return false;
        }
    }

    public AiModelConfig? ResolveModel(ModelRole role, bool isFallback = false)
    {
        string targetId = role switch
        {
            ModelRole.FastSolver => isFallback ? SchedulerConfig.FastSolverFallbackModelId : SchedulerConfig.FastSolverModelId,
            ModelRole.Vision => isFallback ? SchedulerConfig.VisionFallbackModelId : SchedulerConfig.VisionModelId,
            ModelRole.DeepReasoning => isFallback ? SchedulerConfig.DeepReasoningFallbackModelId : SchedulerConfig.DeepReasoningModelId,
            ModelRole.DocumentParser => isFallback ? SchedulerConfig.DocumentParserFallbackModelId : SchedulerConfig.DocumentParserModelId,
            _ => SchedulerConfig.FastSolverModelId
        };

        var model = Models.FirstOrDefault(x => x.Id == targetId && x.IsEnabled);
        if (model != null) return model;

        // Auto fallback to any available healthy model
        if (role == ModelRole.Vision)
            return Models.FirstOrDefault(x => x.SupportsVision && x.IsEnabled) ?? Models.FirstOrDefault(x => x.IsEnabled);

        return Models.FirstOrDefault(x => x.IsEnabled);
    }
}
