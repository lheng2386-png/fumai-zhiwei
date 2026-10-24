using System;
using System.Globalization;
using UnityEngine;
using UnityEngine.Rendering;

namespace FumaiTwin
{
    [Serializable] public class Motion { public float x; public float y; }
    public class FumaiOrbit : MonoBehaviour
    {
        public Vector3 target = new Vector3(0, 1, 0);
        public float distance = 115, yaw = 38, pitch = 29;
        Vector3 fromPosition, fromTarget, desiredTarget;
        float elapsed = 1;
        float lastInteraction;
        public void Wake() { lastInteraction = Time.unscaledTime; OnDemandRendering.renderFrameInterval = 1; }
        void LateUpdate()
        {
            Vector3 offset = Quaternion.Euler(pitch, yaw, 0) * new Vector3(0, 0, -distance);
            if (elapsed < 1)
            {
                elapsed = Mathf.Min(1, elapsed + Time.unscaledDeltaTime / .65f);
                float blend = 1 - Mathf.Pow(1 - elapsed, 3);
                target = Vector3.Lerp(fromTarget, desiredTarget, blend);
                transform.position = Vector3.Lerp(fromPosition, desiredTarget + offset, blend);
            }
            else transform.position = target + offset;
            transform.LookAt(target);
            OnDemandRendering.renderFrameInterval = elapsed < 1 || Time.unscaledTime - lastInteraction < 2 ? 1 : 30;
        }
        public void Orbit(string payload)
        {
            var move = JsonUtility.FromJson<Motion>(payload); if (move == null || !Finite(move.x) || !Finite(move.y)) return;
            Wake(); elapsed = 1; yaw += Mathf.Clamp(move.x, -500, 500) * .22f; pitch = Mathf.Clamp(pitch + move.y * .18f, 4, 86);
        }
        public void Pan(string payload)
        {
            var move = JsonUtility.FromJson<Motion>(payload); if (move == null || !Finite(move.x) || !Finite(move.y)) return;
            Wake(); elapsed = 1; Vector3 right = transform.right; Vector3 forward = Vector3.ProjectOnPlane(transform.forward, Vector3.up).normalized;
            target += (-right * Mathf.Clamp(move.x, -500, 500) + forward * Mathf.Clamp(move.y, -500, 500)) * distance * .001f;
        }
        public void Zoom(string value)
        {
            if (float.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out float amount) && Finite(amount))
            { Wake(); elapsed = 1; distance = Mathf.Clamp(distance * Mathf.Exp(Mathf.Clamp(amount, -500, 500) * .001f), 1.3f, 420); }
        }
        public void Overview() { Move(new Vector3(0, 1, 0), 115, 38, 29); }
        public void Top() { Move(Vector3.zero, 120, 0, 86); }
        public void Walk() { Move(new Vector3(0, 1.6f, -8), 43, 12, 5); }
        public void Focus(AssetHandle asset)
        {
            var box = asset.GetComponent<BoxCollider>();
            Vector3 center = box ? asset.transform.TransformPoint(box.center) : asset.transform.position + Vector3.up;
            float extent = box ? box.size.magnitude : 3;
            Move(center, Mathf.Clamp(extent * 1.55f, 3, 13), 155, asset.kind == "panel" ? 42 : 17);
        }
        void Move(Vector3 point, float range, float angle, float elevation)
        {
            fromPosition = transform.position; fromTarget = target; desiredTarget = point;
            distance = range; yaw = angle; pitch = elevation; elapsed = 0;
            Wake();
        }
        static bool Finite(float number) => !float.IsNaN(number) && !float.IsInfinity(number);
    }
}
