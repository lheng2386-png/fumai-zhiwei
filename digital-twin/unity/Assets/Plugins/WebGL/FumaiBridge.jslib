mergeInto(LibraryManager.library, {
  FumaiNotify: function (message) {
    var text = UTF8ToString(message);
    if (typeof window.fumaiUnityEvent === 'function') window.fumaiUnityEvent(text);
  }
});
