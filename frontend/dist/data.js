/* Constructed demo fixtures. No real measurements, predictions or fault claims. */
window.FumaiDemo = (() => {
    const devices = Array.from({ length: 24 }, (_, i) => ({
        id: `inv_${String(i + 1).padStart(2, '0')}`,
        name: `逆变器 ${String(i + 1).padStart(2, '0')}`,
        peer_group: '2107_ac_reference_ratio',
        expected_kw: +(25.6 + Math.sin(i * 1.4) * 2.1).toFixed(1),
        actual_kw: +(i === 6 ? 9.4 : i === 11 ? 16.2 : i === 18 ? 18.6 : 24.2 + Math.sin(i * 1.4) * 1.7).toFixed(1),
        signal: [6, 11, 18].includes(i) ? 'attention' : 'reference',
    }));
    const definitions = [
        [7, '10:40', 6, 'device_specific_candidate', 9.4, 27.8, 'unreviewed'],
        [12, '11:15', 5, 'device_specific_candidate', 16.2, 26.5, 'unreviewed'],
        [19, '09:25', 4, 'device_specific_candidate', 13.6, 22.4, 'unreviewed'],
        [3, '13:05', 7, 'shared_or_model_candidate', 18.7, 29.2, 'unreviewed'],
        [16, '13:05', 7, 'shared_or_model_candidate', 17.9, 28.4, 'unreviewed'],
        [22, '14:10', 3, 'insufficient_peers', 10.1, 21.3, 'unreviewed'],
        [9, '08:35', 4, 'device_specific_candidate', 5.8, 16.7, 'unreviewed'],
        [4, '15:20', 5, 'shared_or_model_candidate', 8.2, 15.4, 'unreviewed'],
        [7, '08:10', 3, 'device_specific_candidate', 4.6, 14.7, 'needs_investigation'],
        [14, '12:20', 4, 'shared_or_model_candidate', 19.3, 29.6, 'expected_operation'],
        [21, '07:40', 3, 'insufficient_peers', 2.1, 10.8, 'data_issue'],
        [2, '14:45', 4, 'shared_or_model_candidate', 8.4, 18.7, 'needs_investigation'],
    ];
    const addMinutes = (time, minutes) => {
        const [h, m] = time.split(':').map(Number);
        const n = h * 60 + m + minutes;
        return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
    };
    const events = definitions.map(([inv, start, samples, evidence, actual, reference, status], i) => ({
        event_id: `DEMO-2107-${String(i + 1).padStart(3, '0')}`,
        inverter: `inv_${String(inv).padStart(2, '0')}`,
        peer_group: '2107_ac_reference_ratio',
        start_local: `2023-07-18 ${start}:00`,
        last_sample_local: `2023-07-18 ${addMinutes(start, (samples - 1) * 5)}:00`,
        samples, observed_span_minutes: (samples - 1) * 5,
        mean_actual_kw: actual, mean_reference_kw: reference, threshold_kw: 6.1,
        reference_shortfall_kwh: +((reference - actual) * (samples - 1) * 5 / 60).toFixed(2),
        peer_flag_samples: evidence === 'device_specific_candidate' ? samples : 0,
        min_peer_count: evidence === 'insufficient_peers' ? 3 : 23,
        mean_peer_gap: evidence === 'device_specific_candidate' ? +(.25 + i * .005).toFixed(2) : .08,
        evidence, review_status: status,
        note: status === 'expected_operation' ? '【示例复核】核对后属于计划限发，仍需正式工单确认。' : status === 'data_issue' ? '【示例复核】同群观测缺失，建议核对通信与测点。' : status === 'needs_investigation' ? '【示例复核】已加入后续核查清单，等待现场记录。' : '',
    }));
    const power = Array.from({ length: 145 }, (_, i) => {
        const expected = Math.max(0, Math.sin(i / 144 * Math.PI) * 638 * (1 + .025 * Math.sin(i / 9)));
        const ratio = .958 - .021 * Math.sin(i / 7) - (i > 54 && i < 68 ? .07 : 0) - (i > 82 && i < 91 ? .135 : 0);
        return { time: addMinutes('06:00', i * 5), expected: +expected.toFixed(2), actual: +(expected * ratio).toFixed(2) };
    });
    const energy = key => power.slice(1).reduce((sum, p, i) => sum + (p[key] + power[i][key]) / 2 * 5 / 60, 0);
    return Object.freeze({ devices, events, power, energy, addMinutes, source: 'constructed_demo', timezone: 'PST8PDT', date: '2023-07-18' });
})();
