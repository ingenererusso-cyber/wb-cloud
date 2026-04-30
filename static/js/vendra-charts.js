(function () {
    if (!window.echarts) {
        return;
    }

    const chartRegistry = new WeakMap();

    function ensureChart(element) {
        if (!element) return null;
        let chart = chartRegistry.get(element);
        if (!chart) {
            chart = window.echarts.init(element, null, { renderer: "canvas" });
            chartRegistry.set(element, chart);
        }
        return chart;
    }

    function formatMoney(value) {
        return `${Number(value || 0).toFixed(2)} ₽`;
    }

    function installAutoResize(element, chart) {
        if (!element || !chart || element.__vendraResizeInstalled) {
            return;
        }
        const resize = () => chart.resize();
        if (window.ResizeObserver) {
            const observer = new ResizeObserver(resize);
            observer.observe(element);
            element.__vendraResizeObserver = observer;
        } else {
            window.addEventListener("resize", resize);
        }
        element.__vendraResizeInstalled = true;
    }

    function renderStorageHistoryChart(element, points, options) {
        if (!element) return;
        if (!Array.isArray(points) || !points.length) {
            element.innerHTML = '<div class="empty">Нет данных для графика.</div>';
            return;
        }

        const chart = ensureChart(element);
        if (!chart) return;
        installAutoResize(element, chart);

        const colorMain = (options && options.colorMain) || "#15803d";
        const colorSoft = (options && options.colorSoft) || "rgba(21, 128, 61, 0.14)";
        const labels = points.map((point) => point.label);
        const values = points.map((point) => Number(point.daily_storage_cost || 0));

        chart.setOption(
            {
                animationDuration: 500,
                animationDurationUpdate: 350,
                grid: { left: 16, right: 18, top: 28, bottom: 26, containLabel: true },
                tooltip: {
                    trigger: "axis",
                    backgroundColor: "rgba(19, 32, 51, 0.96)",
                    borderWidth: 0,
                    textStyle: { color: "#f8fafc", fontSize: 12 },
                    padding: [10, 12],
                    axisPointer: {
                        type: "line",
                        lineStyle: { color: "rgba(21, 128, 61, 0.22)", width: 1.5 },
                    },
                    formatter(params) {
                        const item = Array.isArray(params) ? params[0] : params;
                        return `<strong>${item.axisValue}</strong><br>${formatMoney(item.data)}`;
                    },
                },
                xAxis: {
                    type: "category",
                    boundaryGap: false,
                    data: labels,
                    axisLine: { lineStyle: { color: "rgba(19, 32, 51, 0.16)" } },
                    axisTick: { show: false },
                    axisLabel: { color: "#64748b", fontSize: 11, margin: 10 },
                },
                yAxis: {
                    type: "value",
                    splitNumber: 4,
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: {
                        color: "#64748b",
                        fontSize: 11,
                        formatter(value) {
                            return `${Math.round(Number(value || 0))} ₽`;
                        },
                    },
                    splitLine: { lineStyle: { color: "rgba(19, 32, 51, 0.08)" } },
                },
                series: [
                    {
                        type: "line",
                        smooth: 0.38,
                        symbol: "circle",
                        symbolSize: 8,
                        data: values,
                        lineStyle: { width: 3, color: colorMain },
                        itemStyle: {
                            color: "#ffffff",
                            borderColor: colorMain,
                            borderWidth: 3,
                        },
                        areaStyle: {
                            color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: colorSoft },
                                { offset: 1, color: "rgba(255,255,255,0)" },
                            ]),
                        },
                    },
                ],
            },
            true,
        );
    }

    function renderLineTrendChart(element, points, options) {
        if (!element) return;
        if (!Array.isArray(points) || !points.length) {
            element.innerHTML = `<div class="chart-empty">${(options && options.emptyText) || "Нет данных для графика."}</div>`;
            return;
        }

        const chart = ensureChart(element);
        if (!chart) return;
        installAutoResize(element, chart);

        const labels = points.map((point) => point.label);
        const valueKey = options && options.valueKey ? options.valueKey : "value";
        const rawValues = points.map((point) => Number(point[valueKey]));
        const values = rawValues.filter((value) => Number.isFinite(value));
        if (!values.length) {
            element.innerHTML = `<div class="chart-empty">${(options && options.emptyText) || "Нет данных для графика."}</div>`;
            return;
        }

        const colorMain = (options && options.colorMain) || "#14532d";
        const colorSoft = (options && options.colorSoft) || "rgba(20, 83, 45, 0.14)";
        const min = typeof options?.min === "number" ? options.min : null;
        const max = typeof options?.max === "number" ? options.max : null;

        chart.setOption(
            {
                animationDuration: 500,
                animationDurationUpdate: 350,
                grid: { left: 18, right: 18, top: 26, bottom: 26, containLabel: true },
                tooltip: {
                    trigger: "axis",
                    backgroundColor: "rgba(19, 32, 51, 0.96)",
                    borderWidth: 0,
                    textStyle: { color: "#f8fafc", fontSize: 12 },
                    padding: [10, 12],
                    axisPointer: {
                        type: "line",
                        lineStyle: { color: "rgba(20, 83, 45, 0.22)", width: 1.5 },
                    },
                    formatter(params) {
                        const item = Array.isArray(params) ? params[0] : params;
                        if (options && typeof options.tooltipFormatter === "function") {
                            return options.tooltipFormatter(points[item.dataIndex], item.data);
                        }
                        return `<strong>${item.axisValue}</strong><br>${item.data}`;
                    },
                },
                xAxis: {
                    type: "category",
                    boundaryGap: false,
                    data: labels,
                    axisLine: { lineStyle: { color: "rgba(19, 32, 51, 0.16)" } },
                    axisTick: { show: false },
                    axisLabel: { color: "#64748b", fontSize: 11, margin: 10 },
                },
                yAxis: {
                    type: "value",
                    min,
                    max,
                    splitNumber: 4,
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: {
                        color: "#64748b",
                        fontSize: 11,
                        formatter(value) {
                            if (options && typeof options.tickFormatter === "function") {
                                return options.tickFormatter(value);
                            }
                            return String(value);
                        },
                    },
                    splitLine: { lineStyle: { color: "rgba(19, 32, 51, 0.08)" } },
                },
                series: [
                    {
                        type: "line",
                        smooth: 0.35,
                        symbol: "circle",
                        symbolSize: 7,
                        data: rawValues.map((value) => (Number.isFinite(value) ? value : null)),
                        lineStyle: { width: 3, color: colorMain },
                        itemStyle: {
                            color: "#ffffff",
                            borderColor: colorMain,
                            borderWidth: 3,
                        },
                        areaStyle: {
                            color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: colorSoft },
                                { offset: 1, color: "rgba(255,255,255,0)" },
                            ]),
                        },
                    },
                ],
            },
            true,
        );
    }

    function renderHorizontalBarChart(element, points, options) {
        if (!element) return;
        if (!Array.isArray(points) || !points.length) {
            element.innerHTML = `<div class="chart-empty">${(options && options.emptyText) || "Нет данных для диаграммы."}</div>`;
            return;
        }

        const chart = ensureChart(element);
        if (!chart) return;
        installAutoResize(element, chart);

        const categoryKey = options?.categoryKey || "label";
        const valueKey = options?.valueKey || "value";
        const labels = points.map((point) => point[categoryKey]);
        const values = points.map((point) => Number(point[valueKey] || 0));
        const colorMain = (options && options.colorMain) || "#b91c1c";

        chart.setOption(
            {
                animationDuration: 450,
                animationDurationUpdate: 300,
                grid: { left: 18, right: 18, top: 18, bottom: 10, containLabel: true },
                tooltip: {
                    trigger: "axis",
                    axisPointer: { type: "shadow", shadowStyle: { color: "rgba(19, 32, 51, 0.04)" } },
                    backgroundColor: "rgba(19, 32, 51, 0.96)",
                    borderWidth: 0,
                    textStyle: { color: "#f8fafc", fontSize: 12 },
                    padding: [10, 12],
                    formatter(params) {
                        const item = Array.isArray(params) ? params[0] : params;
                        if (options && typeof options.tooltipFormatter === "function") {
                            return options.tooltipFormatter(points[item.dataIndex], item.data);
                        }
                        return `<strong>${item.name}</strong><br>${item.data}`;
                    },
                },
                xAxis: {
                    type: "value",
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: { color: "#64748b", fontSize: 11 },
                    splitLine: { lineStyle: { color: "rgba(19, 32, 51, 0.08)" } },
                },
                yAxis: {
                    type: "category",
                    data: labels,
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: { color: "#334155", fontSize: 12, width: 140, overflow: "truncate" },
                },
                series: [
                    {
                        type: "bar",
                        data: values,
                        barWidth: 14,
                        showBackground: true,
                        backgroundStyle: {
                            color: "rgba(19, 32, 51, 0.08)",
                            borderRadius: [999, 999, 999, 999],
                        },
                        itemStyle: {
                            borderRadius: [999, 999, 999, 999],
                            color: new window.echarts.graphic.LinearGradient(1, 0, 0, 0, [
                                { offset: 0, color: colorMain },
                                { offset: 1, color: "#f87171" },
                            ]),
                        },
                        label: {
                            show: true,
                            position: "right",
                            color: "#64748b",
                            fontSize: 11,
                            formatter(params) {
                                if (options && typeof options.labelFormatter === "function") {
                                    return options.labelFormatter(points[params.dataIndex], params.value);
                                }
                                return params.value;
                            },
                        },
                    },
                ],
            },
            true,
        );
    }

    function renderComparisonTrendChart(element, payload, options) {
        if (!element) return;
        const labels = Array.isArray(payload?.labels) ? payload.labels : [];
        const currentValues = Array.isArray(payload?.current_values) ? payload.current_values.map((v) => Number(v || 0)) : [];
        const previousValues = Array.isArray(payload?.previous_values) ? payload.previous_values.map((v) => Number(v || 0)) : [];
        const pointsCount = Math.min(labels.length, currentValues.length, previousValues.length);
        if (!pointsCount) {
            element.innerHTML = `<div class="chart-empty">${(options && options.emptyText) || "Недостаточно данных."}</div>`;
            return;
        }

        const chart = ensureChart(element);
        if (!chart) return;
        installAutoResize(element, chart);

        const currentColor = options?.currentColor || "#14532d";
        const previousColor = options?.previousColor || "#94a3b8";
        const currentLabel = options?.currentLabel || "Текущий период";
        const previousLabel = options?.previousLabel || "Предыдущий период";

        chart.setOption(
            {
                animationDuration: 450,
                animationDurationUpdate: 300,
                grid: { left: 18, right: 18, top: 38, bottom: 24, containLabel: true },
                legend: {
                    top: 2,
                    left: 0,
                    itemWidth: 12,
                    itemHeight: 12,
                    textStyle: { color: "#64748b", fontSize: 12, fontWeight: 600 },
                    data: [currentLabel, previousLabel],
                },
                tooltip: {
                    trigger: "axis",
                    backgroundColor: "rgba(19, 32, 51, 0.96)",
                    borderWidth: 0,
                    textStyle: { color: "#f8fafc", fontSize: 12 },
                    padding: [10, 12],
                    axisPointer: {
                        type: "line",
                        lineStyle: { color: "rgba(20, 83, 45, 0.18)", width: 1.5 },
                    },
                    formatter(params) {
                        const items = Array.isArray(params) ? params : [params];
                        const idx = items[0]?.dataIndex ?? 0;
                        const lines = [`<strong>${labels[idx]}</strong>`];
                        items.forEach((item) => {
                            lines.push(`${item.seriesName}: ${item.data}`);
                        });
                        return lines.join("<br>");
                    },
                },
                xAxis: {
                    type: "category",
                    boundaryGap: false,
                    data: labels.slice(0, pointsCount),
                    axisLine: { lineStyle: { color: "rgba(19, 32, 51, 0.16)" } },
                    axisTick: { show: false },
                    axisLabel: { color: "#64748b", fontSize: 11, margin: 10 },
                },
                yAxis: {
                    type: "value",
                    splitNumber: 4,
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: { color: "#64748b", fontSize: 11 },
                    splitLine: { lineStyle: { color: "rgba(19, 32, 51, 0.08)" } },
                },
                series: [
                    {
                        name: previousLabel,
                        type: "line",
                        smooth: 0.32,
                        symbol: "circle",
                        symbolSize: 6,
                        data: previousValues.slice(0, pointsCount),
                        lineStyle: { width: 2, color: previousColor },
                        itemStyle: {
                            color: "#ffffff",
                            borderColor: previousColor,
                            borderWidth: 2,
                        },
                    },
                    {
                        name: currentLabel,
                        type: "line",
                        smooth: 0.32,
                        symbol: "circle",
                        symbolSize: 7,
                        data: currentValues.slice(0, pointsCount),
                        lineStyle: { width: 3, color: currentColor },
                        itemStyle: {
                            color: "#ffffff",
                            borderColor: currentColor,
                            borderWidth: 3,
                        },
                        areaStyle: {
                            color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: "rgba(20, 83, 45, 0.14)" },
                                { offset: 1, color: "rgba(255,255,255,0)" },
                            ]),
                        },
                    },
                ],
            },
            true,
        );
    }

    window.VendraCharts = {
        renderComparisonTrendChart,
        renderHorizontalBarChart,
        renderLineTrendChart,
        renderStorageHistoryChart,
        formatMoney,
    };
})();
