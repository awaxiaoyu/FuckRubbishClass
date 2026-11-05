using FuckRubbishClass.ViewModels;
using System.Windows;
using System.Windows.Controls;

namespace FuckRubbishClass.Views;

public partial class CourseHubView : UserControl
{
    public CourseHubView()
    {
        InitializeComponent();
    }

    private void OnFilterClicked(object sender, RoutedEventArgs e)
    {
        if (sender is RadioButton rb && rb.Tag is string filter && DataContext is CourseHubViewModel vm)
        {
            vm.SelectedPlatformFilter = filter;
        }
    }
}
