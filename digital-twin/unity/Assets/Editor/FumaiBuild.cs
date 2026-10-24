using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using FumaiTwin;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

public static class FumaiBuild
{
    const string Generated = "Assets/FumaiGenerated";
    const string ScenePath = Generated + "/Plant.unity";
    static string Root => Path.GetFullPath(Path.Combine(Application.dataPath, "../.."));
    static string Hash(string path)
    {
        using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path))
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }
    static void Check(bool ok, string message) { if (!ok) throw new InvalidOperationException(message); }

    static string ImportMaxModels(Recipe recipe)
    {
        string maxRoot = Path.Combine(Root, "artifacts/max");
        string pointerPath = Path.Combine(maxRoot, "latest.json");
        Check(File.Exists(pointerPath), "3ds Max export has not run. Run 3dsmax/BuildEquipment.ms in licensed 3ds Max first.");
        var pointer = JsonUtility.FromJson<ExportPointer>(File.ReadAllText(pointerPath));
        Check(pointer != null && Regex.IsMatch(pointer.relative_directory ?? "", @"^\d{8}T\d{12}Z$"), "Invalid Max export directory.");
        string input = Path.Combine(maxRoot, pointer.relative_directory);
        string receiptPath = Path.Combine(input, "max-export.json");
        var receipt = JsonUtility.FromJson<ExportReceipt>(File.ReadAllText(receiptPath));
        Check(receipt != null && receipt.schema_version == 1 && receipt.runtime == "Autodesk 3ds Max / pymxs", "Missing native Max provenance.");
        Check(receipt.source_sha256 == Hash(Path.Combine(Root, "source/equipment-recipe.json")), "Model recipe changed after Max export; rebuild Max models.");
        string models = Generated + "/Models";
        Directory.CreateDirectory(models);
        Check(receipt.files != null && receipt.files.Length == recipe.models.Length * 2, "Incomplete LOD export.");
        foreach (var model in recipe.models) foreach (int lod in new[] { 0, 1 })
        {
            var file = receipt.files.Single(f => f.model == model.id && f.lod == lod);
            string stem = model.id + "_LOD" + lod;
            Check(file.fbx == stem + ".fbx" && file.max == stem + ".max", "Invalid export filename.");
            string fbx = Path.Combine(input, file.fbx), max = Path.Combine(input, file.max);
            Check(File.Exists(fbx) && File.Exists(max), "Native MAX/FBX pair missing: " + stem);
            Check(Hash(fbx) == file.fbx_sha256 && Hash(max) == file.max_sha256, "Native asset hash mismatch: " + stem);
            File.Copy(fbx, models + "/" + file.fbx, true);
        }
        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        foreach (string path in Directory.GetFiles(models, "*.fbx"))
        {
            var importer = AssetImporter.GetAtPath(path.Replace('\\', '/')) as ModelImporter;
            Check(importer != null, "FBX importer failed: " + path);
            importer.globalScale = 1;
            importer.useFileScale = true;
            importer.importCameras = false;
            importer.importLights = false;
            importer.importAnimation = false;
            importer.materialImportMode = ModelImporterMaterialImportMode.None;
            importer.SaveAndReimport();
        }
        return receiptPath;
    }

    static Material Surface(string name, Color color, float metallic = 0, float smoothness = .25f)
    {
        var mat = new Material(Shader.Find("Standard")) { name = name, color = color, enableInstancing = true };
        mat.EnableKeyword("_EMISSION"); mat.SetFloat("_Metallic", metallic); mat.SetFloat("_Glossiness", smoothness);
        return mat;
    }
    static Dictionary<string, Material> Materials(Recipe recipe)
    {
        var result = new Dictionary<string, Material>();
        foreach (var spec in recipe.materials)
        {
            var mat = Surface(spec.id, new Color(spec.color[0], spec.color[1], spec.color[2]), spec.metallic, 1 - spec.roughness);
            AssetDatabase.CreateAsset(mat, Generated + "/" + spec.id + ".mat"); result.Add(spec.id, mat);
        }
        return result;
    }
    static GameObject Device(string type, Dictionary<string, Material> materials)
    {
        var root = new GameObject(type);
        var group = root.AddComponent<LODGroup>();
        var levels = new LOD[2];
        for (int level = 0; level < 2; level++)
        {
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(Generated + "/Models/" + type + "_LOD" + level + ".fbx");
            Check(model != null, "Missing imported model: " + type);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
            instance.transform.SetParent(root.transform, false);
            foreach (var renderer in instance.GetComponentsInChildren<Renderer>())
            {
                string key = renderer.gameObject.name.StartsWith(type + "_") ? renderer.gameObject.name.Substring(type.Length + 1) : "";
                Check(materials.ContainsKey(key), "Unexpected material part from Max: " + renderer.name);
                renderer.sharedMaterial = materials[key];
            }
            levels[level] = new LOD(level == 0 ? .12f : .001f, instance.GetComponentsInChildren<Renderer>());
        }
        group.SetLODs(levels); group.RecalculateBounds();
        return root;
    }
    static GameObject Block(string name, Vector3 position, Vector3 size, Material material, Transform parent)
    {
        var block = GameObject.CreatePrimitive(PrimitiveType.Cube); block.name = name;
        block.transform.SetParent(parent, false); block.transform.localPosition = position; block.transform.localScale = size;
        block.GetComponent<Renderer>().sharedMaterial = material;
        UnityEngine.Object.DestroyImmediate(block.GetComponent<Collider>());
        return block;
    }
    static void Landscape(Transform parent)
    {
        var soil = Surface("dry soil and gravel", new Color(.71f, .66f, .51f), 0, .07f);
        var road = Surface("maintenance gravel", new Color(.68f, .69f, .63f), 0, .1f);
        var fence = Surface("galvanized fence", new Color(.42f, .48f, .44f), .65f, .3f);
        var texture = new Texture2D(512, 512, TextureFormat.RGB24, true) { wrapMode = TextureWrapMode.Repeat };
        var random = new System.Random(7407);
        for (int y = 0; y < 512; y++) for (int x = 0; x < 512; x++)
        {
            float field = Mathf.PerlinNoise(x / 65f, y / 65f), grit = (float)random.NextDouble() * .17f;
            texture.SetPixel(x, y, new Color(.62f + field * .16f + grit, .57f + field * .17f + grit, .43f + field * .16f + grit));
        }
        texture.Apply(); soil.mainTexture = texture; soil.mainTextureScale = new Vector2(110, 110);
        AssetDatabase.CreateAsset(texture, Generated + "/GroundTexture.asset");
        AssetDatabase.CreateAsset(soil, Generated + "/Ground.mat"); AssetDatabase.CreateAsset(road, Generated + "/Road.mat"); AssetDatabase.CreateAsset(fence, Generated + "/Fence.mat");
        Block("land surface", new Vector3(0, -.14f, 0), new Vector3(900, .20f, 900), soil, parent);
        Block("maintenance road", new Vector3(0, -.015f, 0), new Vector3(108, .05f, 4), road, parent);
        Block("distribution access", new Vector3(39, -.01f, 0), new Vector3(4, .06f, 91), road, parent);
        for (int side = -1; side <= 1; side += 2)
        {
            for (int i = 0; i <= 40; i++) Block("fence post", new Vector3(-52 + i * 2.6f, 1, side * 44), new Vector3(.06f, 2, .06f), fence, parent);
            for (int i = 0; i <= 34; i++) Block("fence post", new Vector3(side * 52, 1, -44 + i * 2.59f), new Vector3(.06f, 2, .06f), fence, parent);
            foreach (float y in new[] { .3f, 1.05f, 1.8f })
            {
                Block("fence rail", new Vector3(0, y, side * 44), new Vector3(104, .025f, .025f), fence, parent);
                Block("fence rail", new Vector3(side * 52, y, 0), new Vector3(.025f, .025f, 88), fence, parent);
            }
        }
        var wall = Surface("service building", new Color(.68f, .71f, .64f)); AssetDatabase.CreateAsset(wall, Generated + "/Building.mat");
        Block("service building", new Vector3(-46, 2, -54), new Vector3(10, 4, 6), wall, parent);
        Block("service roof", new Vector3(-46, 4.1f, -54), new Vector3(10.7f, .25f, 6.7f), fence, parent);
        for (int i = 0; i < 4; i++) Block("building windows", new Vector3(-49 + i * 2, 2.4f, -50.98f), new Vector3(1.1f, 1.2f, .04f), fence, parent);
        Grass(parent);
    }

    static void Grass(Transform parent)
    {
        var texture = new Texture2D(64, 128, TextureFormat.RGBA32, true) { wrapMode = TextureWrapMode.Clamp };
        var random = new System.Random(8227);
        var origins = Enumerable.Range(0, 15).Select(_ => (float)random.NextDouble() * 64).ToArray();
        var heights = Enumerable.Range(0, 15).Select(_ => 45f + (float)random.NextDouble() * 80).ToArray();
        var bends = Enumerable.Range(0, 15).Select(_ => ((float)random.NextDouble() - .5f) * 30).ToArray();
        for (int y = 0; y < 128; y++) for (int x = 0; x < 64; x++)
        {
            Color pixel = Color.clear;
            for (int blade = 0; blade < origins.Length; blade++)
            {
                float t = y / heights[blade]; if (t >= 1) continue;
                if (Mathf.Abs(x - origins[blade] - bends[blade] * t * t) < (1 - t) * 2)
                    pixel = Color.Lerp(new Color(.34f, .36f, .16f), new Color(.52f, .60f, .27f), t);
            }
            texture.SetPixel(x, y, pixel);
        }
        texture.Apply(); AssetDatabase.CreateAsset(texture, Generated + "/GrassTexture.asset");
        var material = Surface("dry grass", Color.white, 0, .05f); material.mainTexture = texture;
        material.SetFloat("_Mode", 1); material.SetFloat("_Cutoff", .45f); material.EnableKeyword("_ALPHATEST_ON"); material.SetOverrideTag("RenderType", "TransparentCutout"); material.renderQueue = 2450;
        AssetDatabase.CreateAsset(material, Generated + "/Grass.mat");
        var vertices = new List<Vector3>(); var normals = new List<Vector3>(); var uv = new List<Vector2>(); var triangles = new List<int>();
        for (int plane = 0; plane < 2; plane++) for (int side = 0; side < 2; side++)
        {
            int index = vertices.Count; var rotation = Quaternion.Euler(0, plane * 90, 0);
            foreach (var vertex in new[] { new Vector3(-.35f, 0, 0), new Vector3(.35f, 0, 0), new Vector3(.35f, .5f, 0), new Vector3(-.35f, .5f, 0) }) vertices.Add(rotation * vertex);
            for (int i = 0; i < 4; i++) normals.Add(rotation * (side == 0 ? Vector3.forward : Vector3.back));
            uv.AddRange(new[] { Vector2.zero, Vector2.right, Vector2.one, Vector2.up });
            triangles.AddRange((side == 0 ? new[] { 0, 1, 2, 0, 2, 3 } : new[] { 2, 1, 0, 3, 2, 0 }).Select(i => index + i));
        }
        var mesh = new Mesh { name = "cross grass cards" }; mesh.SetVertices(vertices); mesh.SetNormals(normals); mesh.SetUVs(0, uv); mesh.SetTriangles(triangles, 0); mesh.RecalculateBounds();
        AssetDatabase.CreateAsset(mesh, Generated + "/GrassMesh.asset");
        for (int i = 0; i < 1400; i++)
        {
            float x = ((float)random.NextDouble() - .5f) * 140, z = ((float)random.NextDouble() - .5f) * 115;
            if (Mathf.Abs(z) < 2.4f || Mathf.Abs(x - 39) < 2.5f) continue;
            var clump = new GameObject("grass clump"); clump.transform.SetParent(parent, false); clump.transform.position = new Vector3(x, -.015f, z); clump.transform.rotation = Quaternion.Euler(0, (float)random.NextDouble() * 360, 0); clump.transform.localScale = Vector3.one * (.5f + (float)random.NextDouble());
            clump.AddComponent<MeshFilter>().sharedMesh = mesh; var renderer = clump.AddComponent<MeshRenderer>(); renderer.sharedMaterial = material; renderer.shadowCastingMode = ShadowCastingMode.Off;
        }
    }

    [MenuItem("Fumai/Build native Max scene")]
    public static void Setup()
    {
        string source = Path.Combine(Root, "source");
        var recipe = JsonUtility.FromJson<Recipe>(File.ReadAllText(Path.Combine(source, "equipment-recipe.json")));
        var layout = JsonUtility.FromJson<Layout>(File.ReadAllText(Path.Combine(source, "layout.json")));
        Check(recipe.version == 1 && layout.version == 1 && recipe.units == "m" && layout.units == "m" && recipe.up == "Y" && layout.up == "Y", "Unsupported coordinate contract.");
        string nativeReceipt = ImportMaxModels(recipe);
        Directory.CreateDirectory(Generated);
        Directory.CreateDirectory("Assets/StreamingAssets");
        File.Copy(Path.Combine(source, "layout.json"), "Assets/StreamingAssets/plant-layout.json", true);
        // Only names created by this generator are replaced. Do not reset an open scene.
        foreach (var path in Directory.GetFiles(Generated, "*.mat")) AssetDatabase.DeleteAsset(path.Replace('\\', '/'));
        foreach (var path in Directory.GetFiles(Generated, "*.asset")) AssetDatabase.DeleteAsset(path.Replace('\\', '/'));
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
        SceneManager.SetActiveScene(scene);
        var materials = Materials(recipe);
        var site = new GameObject("Fumai illustrative site"); Landscape(site.transform);
        var prototypes = recipe.models.ToDictionary(m => m.id, m => Device(m.id, materials));
        foreach (var asset in layout.assets)
        {
            Check(Regex.IsMatch(asset.id, @"^[A-Za-z0-9_-]{1,64}$") && prototypes.ContainsKey(asset.type), "Invalid asset identity.");
            var obj = UnityEngine.Object.Instantiate(prototypes[asset.type], site.transform);
            obj.name = asset.id; obj.transform.position = Coordinates.Vector(asset.position); obj.transform.rotation = Quaternion.Euler(Coordinates.Vector(asset.rotation));
            var handle = obj.AddComponent<AssetHandle>(); handle.id = asset.id; handle.kind = asset.type; handle.source = layout.source;
            var spec = recipe.models.Single(m => m.id == asset.type); var collider = obj.AddComponent<BoxCollider>();
            collider.size = Coordinates.Vector(spec.dimensions_m); collider.center = asset.type == "panel" ? Vector3.zero : new Vector3(0, collider.size.y / 2, 0);
            if (asset.type == "panel")
            {
                var rack = UnityEngine.Object.Instantiate(prototypes["rack"], site.transform); rack.name = asset.id + "_support";
                rack.transform.position = new Vector3(obj.transform.position.x, 0, obj.transform.position.z);
            }
        }
        foreach (var obj in prototypes.Values) UnityEngine.Object.DestroyImmediate(obj);
        var sun = new GameObject("Sun").AddComponent<Light>(); sun.type = LightType.Directional; sun.intensity = 1.7f; sun.color = new Color(1, .95f, .84f); sun.shadows = LightShadows.Soft; sun.transform.rotation = Quaternion.Euler(38, -35, 0);
        RenderSettings.sun = sun; RenderSettings.ambientMode = AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = new Color(.57f, .69f, .79f); RenderSettings.ambientEquatorColor = new Color(.64f, .65f, .54f); RenderSettings.ambientGroundColor = new Color(.28f, .32f, .24f);
        var sky = new Material(Shader.Find("Skybox/Procedural")); sky.SetFloat("_SunSize", .035f); sky.SetFloat("_AtmosphereThickness", 1.1f);
        AssetDatabase.CreateAsset(sky, Generated + "/Sky.mat"); RenderSettings.skybox = sky;
        RenderSettings.fog = true; RenderSettings.fogMode = FogMode.Linear; RenderSettings.fogColor = new Color(.72f, .79f, .81f); RenderSettings.fogStartDistance = 130; RenderSettings.fogEndDistance = 650;
        QualitySettings.shadows = ShadowQuality.All; QualitySettings.shadowResolution = ShadowResolution.High; QualitySettings.shadowDistance = 110; QualitySettings.antiAliasing = 4;
        var camera = new GameObject("Main Camera").AddComponent<Camera>(); camera.tag = "MainCamera"; camera.nearClipPlane = .08f; camera.farClipPlane = 1000; camera.fieldOfView = 46; camera.clearFlags = CameraClearFlags.Skybox;
        camera.gameObject.AddComponent<FumaiOrbit>(); new GameObject("FumaiBridge").AddComponent<FumaiPlant>();
        EditorSceneManager.SaveScene(scene, ScenePath);
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
        AssetDatabase.SaveAssets();
        File.WriteAllText(Path.Combine(Root, "artifacts/unity-scene-source.json"), JsonUtility.ToJson(new SceneSource { max_receipt_sha256 = Hash(nativeReceipt), recipe_sha256 = Hash(Path.Combine(source, "equipment-recipe.json")), layout_sha256 = Hash(Path.Combine(source, "layout.json")), scene_assets = layout.assets.Length, unity_version = Application.unityVersion }, true));
        Debug.Log("FUMAI_NATIVE_SCENE_OK " + layout.assets.Length);
    }
    [Serializable] class SceneSource { public string max_receipt_sha256; public string recipe_sha256; public string layout_sha256; public int scene_assets; public string unity_version; }

    [MenuItem("Fumai/Build WebGL Wasm")]
    public static void WebGL()
    {
        Setup();
        PlayerSettings.companyName = "Fumai"; PlayerSettings.productName = "Fumai native scene";
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.WebGL, ScriptingImplementation.IL2CPP);
        PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
        PlayerSettings.WebGL.template = "PROJECT:Fumai";
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions { scenes = new[] { ScenePath }, locationPathName = Path.Combine(Root, "artifacts/WebGL"), target = BuildTarget.WebGL, options = BuildOptions.None });
        Check(report.summary.result == BuildResult.Succeeded, "Unity WebGL build failed: " + report.summary.result);
        Check(Directory.GetFiles(Path.Combine(Root, "artifacts/WebGL/Build"), "*.wasm").Length > 0, "No Wasm output; do not publish.");
        Debug.Log("FUMAI_UNITY_WEBGL_OK " + report.summary.totalSize);
    }
}
