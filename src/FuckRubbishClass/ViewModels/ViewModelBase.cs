using CommunityToolkit.Mvvm.ComponentModel;

namespace FuckRubbishClass.ViewModels;

public abstract partial class ViewModelBase : ObservableObject
{
    [ObservableProperty]
    private bool _isBusy;

    [ObservableProperty]
    private string _busyMessage = "正在处理…";

    [ObservableProperty]
    private string? _errorMessage;

    [ObservableProperty]
    private string? _successMessage;

    public void ClearMessages()
    {
        ErrorMessage = null;
        SuccessMessage = null;
    }
}
