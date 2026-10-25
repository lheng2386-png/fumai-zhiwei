// Visual rehearsal only: no location, date, weather or measured irradiance is available.
export const DAYLIGHT_START = 360;
export const DAYLIGHT_END = 1080;
export const DAYLIGHT_DEFAULT = 720;

/** Minutes are illustrative clock time, independent of telemetry and event timestamps. */
export function daylightSample(value) {
    if (!Number.isFinite(value)) throw new TypeError('日照示意时间必须是有限数字');
    const minute = Math.max(DAYLIGHT_START, Math.min(DAYLIGHT_END, value));
    const phase = (minute - DAYLIGHT_START) / (DAYLIGHT_END - DAYLIGHT_START) * Math.PI;
    const daylight = Math.sin(phase);
    const altitude = 5 + 55 * daylight;
    const elevation = altitude * Math.PI / 180;
    const rounded = Math.floor(minute);
    return {
        minute,
        time: `${String(Math.floor(rounded / 60)).padStart(2, '0')}:${String(rounded % 60).padStart(2, '0')}`,
        altitude,
        sunPosition: [100 * Math.cos(phase) * Math.cos(elevation), 100 * Math.sin(elevation), -100 * Math.sin(phase) * Math.cos(elevation)],
        warmth: Math.max(0, 1 - daylight / .65),
        intensity: 1.3 + 1.9 * daylight,
        ambient: .65 + .5 * daylight,
        exposure: .8 + .28 * daylight,
        simulated: true
    };
}

/** Playback advances 20 illustrative minutes per second and stops at dusk. */
export function advanceDaylight(minute, seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) throw new TypeError('播放时间间隔无效');
    return daylightSample(minute + Math.min(seconds, .1) * 20);
}
