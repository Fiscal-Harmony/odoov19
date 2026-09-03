/** @odoo-module */

import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

const FiscalPDFBusListener = {
    dependencies: ["bus_service", "notification"],

    start(env, { bus_service, notification }) {
        const channel = `pos_order_fiscal_pdf_user_${user.userId}`;
        bus_service.addChannel(channel);

        bus_service.addEventListener("notification", (notifications) => {
            for (const notif of notifications) {
                const [channel_name, payload] = notif;

                if (channel_name === channel && payload && payload.status === 'pdf_ready') {
                    notification.add(payload.message || 'Fiscal PDF is ready', {
                        type: 'success',
                        title: 'Fiscal PDF Ready',
                    });
                }
            }
        });
    },
};

registry.category("services").add("fiscal_pdf_bus_listener", FiscalPDFBusListener);
