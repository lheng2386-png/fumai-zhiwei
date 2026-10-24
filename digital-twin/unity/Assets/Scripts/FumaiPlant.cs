using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using UnityEngine;

namespace FumaiTwin
{
    [Serializable] public class Pick { public float x; public float y; }
    [Serializable] public class ReadyNotice { public string type = "ready"; public string engine = "Unity WebGL / IL2CPP Wasm"; public string source = "constructed_demo"; public int assets; }
    [Serializable] public class MonitorNotice { public string type = "monitoring"; public string source; public string observed_at; public MonitorValue[] values; }
    public class FumaiPlant : MonoBehaviour
    {
        readonly Dictionary<string, AssetHandle> assets = new Dictionary<string, AssetHandle>();
        readonly Dictionary<string, GameObject> markers = new Dictionary<string, GameObject>();
        AssetHandle selected;
        FumaiOrbit orbit;
#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] static extern void FumaiNotify(string message);
#endif
        void Start()
        {
            Application.targetFrameRate = 60;
            orbit = Camera.main.GetComponent<FumaiOrbit>();
            foreach (var asset in FindObjectsByType<AssetHandle>(FindObjectsSortMode.None)) assets.Add(asset.id, asset);
            Notify(JsonUtility.ToJson(new ReadyNotice { assets = assets.Count }));
        }
        void Notify(string message)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            FumaiNotify(message);
#else
            Debug.Log("FUMAI_BROWSER_EVENT " + message);
#endif
        }
        public void SelectAsset(string id)
        {
            if (!Regex.IsMatch(id ?? "", @"^[A-Za-z0-9_-]{1,64}$") || !assets.TryGetValue(id, out var asset)) return;
            if (selected) selected.Highlight(false); selected = asset; selected.Highlight(true); orbit.Focus(asset);
            Notify(JsonUtility.ToJson(new SelectionNotice { id = asset.id, type = asset.kind, source = asset.source }));
        }
        public void PickAt(string payload)
        {
            var point = JsonUtility.FromJson<Pick>(payload);
            if (point == null || float.IsNaN(point.x) || float.IsNaN(point.y) || point.x < 0 || point.x > 1 || point.y < 0 || point.y > 1) return;
            var camera = Camera.main;
            var ray = camera.ScreenPointToRay(new Vector3(point.x * camera.pixelWidth, (1 - point.y) * camera.pixelHeight, 0));
            if (Physics.Raycast(ray, out var hit, 1000))
            { var asset = hit.collider.GetComponentInParent<AssetHandle>(); if (asset) SelectAsset(asset.id); }
        }
        public void View(string mode)
        {
            if (mode == "overview") orbit.Overview(); else if (mode == "top") orbit.Top(); else if (mode == "walk") orbit.Walk();
            else if (mode == "focus" && selected) orbit.Focus(selected);
        }
        public void Orbit(string payload) { if (orbit) orbit.Orbit(payload); }
        public void Pan(string payload) { if (orbit) orbit.Pan(payload); }
        public void Zoom(string payload) { if (orbit) orbit.Zoom(payload); }
        public void Quality(string mode)
        {
            if (orbit) orbit.Wake();
            bool low = mode == "low";
            QualitySettings.shadows = low ? ShadowQuality.Disable : ShadowQuality.All;
            QualitySettings.antiAliasing = low ? 0 : 4;
            QualitySettings.lodBias = low ? .45f : 1f;
        }
        public void Lighting(string mode)
        {
            if (orbit) orbit.Wake();
            bool dusk = mode == "dusk";
            RenderSettings.sun.color = dusk ? new Color(1, .61f, .30f) : new Color(1, .95f, .84f);
            RenderSettings.sun.intensity = dusk ? 1.05f : 1.7f;
            RenderSettings.sun.transform.rotation = Quaternion.Euler(dusk ? 14 : 38, -35, 0);
        }
        public void ApplyMonitoring(string payload)
        {
            if (orbit) orbit.Wake();
            if (string.IsNullOrEmpty(payload) || payload.Length > 131072) return;
            var batch = JsonUtility.FromJson<MonitorBatch>(payload);
            // This recipe has demo identities. Site data requires an independently verified mapping first.
            if (batch == null || batch.source != "constructed_demo" || batch.values == null || batch.values.Length > 1000) return;
            var accepted = new List<MonitorValue>();
            foreach (var value in batch.values)
            {
                if (value == null || !assets.TryGetValue(value.id ?? "", out var asset)) continue;
                if ((value.has_actual_kw && (!Finite(value.actual_kw) || value.actual_kw < 0)) || (value.has_reference_kw && (!Finite(value.reference_kw) || value.reference_kw < 0))) continue;
                accepted.Add(value);
                bool alert = value.state == "unreviewed" || value.state == "needs_investigation";
                if (!markers.TryGetValue(value.id, out var marker))
                {
                    marker = GameObject.CreatePrimitive(PrimitiveType.Sphere); marker.name = value.id + " candidate marker";
                    marker.transform.SetParent(asset.transform, false); marker.transform.localPosition = Vector3.up * (asset.kind == "transformer" ? 4.4f : 2.65f); marker.transform.localScale = Vector3.one * .18f;
                    Destroy(marker.GetComponent<Collider>());
                    var material = new Material(Shader.Find("Standard")); material.color = new Color(.85f, .10f, .06f); material.EnableKeyword("_EMISSION"); material.SetColor("_EmissionColor", new Color(.35f, .025f, .01f)); marker.GetComponent<Renderer>().material = material;
                    markers.Add(value.id, marker);
                }
                marker.SetActive(alert);
            }
            Notify(JsonUtility.ToJson(new MonitorNotice { source = batch.source, observed_at = batch.observed_at, values = accepted.ToArray() }));
        }
        static bool Finite(float n) => !float.IsNaN(n) && !float.IsInfinity(n);
        void OnDestroy() { foreach (var marker in markers.Values) if (marker) Destroy(marker.GetComponent<Renderer>().sharedMaterial); }
    }
}
