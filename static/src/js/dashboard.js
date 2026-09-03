/** @odoo-module */

import { registry } from "@web/core/registry";
import { loadBundle } from "@web/core/assets";
import { rpc } from "@web/core/network/rpc";
import { Component, useState, onWillStart, useRef, useEffect } from "@odoo/owl";

export class FiscalDashboard extends Component {
    static template = "odoov19.FiscalDashboard";
    static props = ["*"];

    setup() {
        this.state = useState({
            loading: true,
            days: 1,
            summary: {
                total: 0,
                fiscalized: 0,
                failed: 0,
                pending: 0,
                sent: 0,
                cancelled: 0,
            },
            dailyData: [],
            commonErrors: [],
        });

        this.chartRef = useRef("pieChart");
        this.chart = null;

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.fetchData();
        });

        useEffect(() => {
            if (this.state.dailyData.length > 0 && !this.state.loading) {
                this.renderChart();
            }
            return () => {
                if (this.chart) {
                    this.chart.destroy();
                    this.chart = null;
                }
            };
        }, () => [this.state.dailyData, this.state.loading]);
    }

    async fetchData() {
        this.state.loading = true;
        try {
            const data = await rpc("/fiscalharmony/dashboard/data", {
                days: this.state.days,
            });
            if (data) {
                this.state.summary = data.summary || this.state.summary;
                this.state.dailyData = data.daily_data || [];
                this.state.commonErrors = data.common_errors || [];
            }
        } catch (e) {
            console.error("Error fetching dashboard data:", e);
        }
        this.state.loading = false;
    }

    async setDays(days) {
        this.state.days = days;
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
        await this.fetchData();
    }

    renderChart() {
        if (!this.chartRef.el) return;

        if (this.chart) {
            this.chart.destroy();
        }

        const ctx = this.chartRef.el.getContext("2d");

        let totalFiscalized = 0;
        let totalFailed = 0;
        let totalPending = 0;
        let totalSent = 0;

        for (const day of this.state.dailyData) {
            totalFiscalized += day.fiscalized || 0;
            totalFailed += day.failed || 0;
            totalSent += (day.total || 0) - (day.fiscalized || 0) - (day.failed || 0);
        }

        this.chart = new Chart(ctx, {
            type: "pie",
            data: {
                labels: ["Fiscalized", "Failed", "Other (Pending/Sent)"],
                datasets: [
                    {
                        data: [totalFiscalized, totalFailed, totalSent],
                        backgroundColor: [
                            "rgba(40, 167, 69, 0.8)",
                            "rgba(220, 53, 69, 0.8)",
                            "rgba(255, 193, 7, 0.8)",
                        ],
                        borderColor: [
                            "rgba(40, 167, 69, 1)",
                            "rgba(220, 53, 69, 1)",
                            "rgba(255, 193, 7, 1)",
                        ],
                        borderWidth: 2,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: "bottom",
                        labels: {
                            padding: 20,
                            usePointStyle: true,
                            font: { size: 13 },
                        },
                    },
                    title: {
                        display: true,
                        text: `Fiscalisation Status (Last ${this.state.days === 0 ? 'All Time' : this.state.days + ' Days'})`,
                        font: { size: 16, weight: "bold" },
                        padding: { bottom: 15 },
                    },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                const value = context.parsed;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                                return `${context.label}: ${value} (${percentage}%)`;
                            },
                        },
                    },
                },
            },
        });
    }
}

registry.category("actions").add("fiscal_dashboard", FiscalDashboard);
