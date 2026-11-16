using FuckRubbishClass.ViewModels;
using System.Windows.Controls;
using Wpf.Ui.Controls;

namespace FuckRubbishClass;

public partial class MainWindow : FluentWindow
{
    private readonly MainViewModel _viewModel;

    public MainWindow()
    {
        InitializeComponent();
        _viewModel = new MainViewModel();
        DataContext = _viewModel;
        NavListBox.SelectionChanged += OnNavSelectionChanged;
        Closed += (_, _) => _viewModel.Dispose();

        _viewModel.DashboardVM.RequestNavigate = tag =>
        {
            _viewModel.Navigate(tag);
            SyncNavListBox(tag);
        };

        Loaded += async (_, _) =>
        {
            await _viewModel.InitializeAsync();
        };
    }

    private void OnNavSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (NavListBox.SelectedItem is ListBoxItem item && item.Tag is string tag)
        {
            _viewModel.Navigate(tag);
        }
    }

    private void SyncNavListBox(string tag)
    {
        foreach (var item in NavListBox.Items.OfType<ListBoxItem>())
        {
            if (item.Tag?.ToString() == tag)
            {
                NavListBox.SelectedItem = item;
                break;
            }
        }
    }
}
