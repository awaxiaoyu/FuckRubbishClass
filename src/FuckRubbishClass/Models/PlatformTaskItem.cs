using System.Text.Json;
using System.Windows.Media;

namespace FuckRubbishClass.Models;

public sealed record PlatformTaskItem(string Title, string Description, JsonElement Payload);
public sealed record HomeworkAnswerItem(string Question, string Answer, string Issue, ImageSource? Image);
