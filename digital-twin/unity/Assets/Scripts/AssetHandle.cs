using UnityEngine;

namespace FumaiTwin
{
    public class AssetHandle : MonoBehaviour
    {
        public string id;
        public string kind;
        public string source;
        public void Highlight(bool selected)
        {
            foreach (var renderer in GetComponentsInChildren<Renderer>(true))
            {
                if (!selected) { renderer.SetPropertyBlock(null); continue; }
                var block = new MaterialPropertyBlock();
                block.SetColor("_EmissionColor", new Color(.22f, .16f, .03f));
                renderer.SetPropertyBlock(block);
            }
        }
    }
}
