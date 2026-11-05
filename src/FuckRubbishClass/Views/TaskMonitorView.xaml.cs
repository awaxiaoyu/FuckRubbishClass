using FuckRubbishClass.ViewModels;
using System.Windows;
using System.Windows.Controls;

namespace FuckRubbishClass.Views;

public partial class TaskMonitorView : UserControl
{
    public TaskMonitorView()
    {
        InitializeComponent();
    }

    private void OnFilterLogLevel(object sender, RoutedEventArgs e)
    {
        if (sender is RadioButton rb && rb.Tag is string level && DataContext is TaskMonitorViewModel vm)
        {
            vm.SelectedLogLevelFilter = level;
        }
    }
}
