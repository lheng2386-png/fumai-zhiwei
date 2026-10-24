using System;
using UnityEngine;

namespace FumaiTwin
{
    [Serializable] public class Recipe { public int version; public string units; public string up; public string source; public string engineering_accuracy; public MaterialSpec[] materials; public ModelSpec[] models; }
    [Serializable] public class MaterialSpec { public string id; public float[] color; public float metallic; public float roughness; }
    [Serializable] public class ModelSpec { public string id; public string name; public float[] dimensions_m; }
    [Serializable] public class Layout { public int version; public string units; public string up; public string source; public AssetSpec[] assets; }
    [Serializable] public class AssetSpec { public string id; public string type; public float[] position; public float[] rotation; public string group; }
    [Serializable] public class ExportPointer { public string relative_directory; }
    [Serializable] public class ExportReceipt { public int schema_version; public string runtime; public string source_sha256; public ExportFile[] files; }
    [Serializable] public class ExportFile { public string model; public int lod; public string fbx; public string fbx_sha256; public string max; public string max_sha256; }
    [Serializable] public class MonitorBatch { public string source; public string observed_at; public MonitorValue[] values; }
    [Serializable] public class MonitorValue { public string id; public string state; public bool has_actual_kw; public float actual_kw; public bool has_reference_kw; public float reference_kw; }
    [Serializable] public class SelectionNotice { public string id; public string type; public string source; }
    public static class Coordinates
    {
        public static Vector3 Vector(float[] value) { return value != null && value.Length == 3 ? new Vector3(value[0], value[1], value[2]) : Vector3.zero; }
    }
}
