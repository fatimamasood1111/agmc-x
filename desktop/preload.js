// Runs in a privileged context with access to Node APIs, but the page
// itself (agmc-assistant-popup.html) never gets that access directly —
// contextBridge exposes only these two specific functions, nothing else.
// Same principle as why get_booking_link is never registered as an LLM
// tool: the smallest possible surface reachable from untrusted content.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  expand: () => ipcRenderer.invoke("widget:expand"),
  collapse: () => ipcRenderer.invoke("widget:collapse"),
});
