using CommunityToolkit.Mvvm.ComponentModel;

namespace FuckRubbishClass.Models;

public partial class ModelSchedulerConfig : ObservableObject
{
    // 角色绑定
    [ObservableProperty]
    private string _fastSolverModelId = string.Empty;

    [ObservableProperty]
    private string _fastSolverFallbackModelId = string.Empty;

    [ObservableProperty]
    private string _visionModelId = string.Empty;

    [ObservableProperty]
    private string _visionFallbackModelId = string.Empty;

    [ObservableProperty]
    private string _deepReasoningModelId = string.Empty;

    [ObservableProperty]
    private string _deepReasoningFallbackModelId = string.Empty;

    [ObservableProperty]
    private string _documentParserModelId = string.Empty;

    [ObservableProperty]
    private string _documentParserFallbackModelId = string.Empty;

    // 调度策略
    [ObservableProperty]
    private bool _autoFallbackOnFailure = true;

    [ObservableProperty]
    private bool _autoUpgradeToVisionOnImages = true;

    [ObservableProperty]
    private int _maxRetryAttempts = 3;

    [ObservableProperty]
    private double _retryBackoffSeconds = 1.5;

    [ObservableProperty]
    private int _maxConcurrentRequests = 3;

    [ObservableProperty]
    private bool _streamResponse = true;

    [ObservableProperty]
    private bool _logAiReasoningChain = true;

    [ObservableProperty]
    private bool _saveQuestionToBank = true;
}
