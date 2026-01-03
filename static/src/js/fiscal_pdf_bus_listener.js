odoo.define('fiscalharmony_zimra_intergration.fiscal_pdf_bus_listener', function (require) {
    "use strict";

    const { registry } = require("@web/core/registry");
    const { onWillStart } = require("@odoo/owl");
    const { useService } = require("@web/core/utils/hooks");

    const FiscalPDFBusListener = {
        dependencies: ["bus", "user", "notification"],

        setup() {
            const bus = useService("bus");
            const user = useService("user");
            const notification = useService("notification");

            onWillStart(() => {
                const channel = `pos_order_fiscal_pdf_user_${user.userId}`;
                bus.addChannel(channel);

                bus.on("notification", null, (notifications) => {
                    for (const notif of notifications) {
                        const [_, channel_name] = notif[0];
                        const payload = notif[1];

                        if (channel_name === channel && payload.status === 'pdf_ready') {
                            notification.add(payload.message, {
                                type: 'success',
                                title: 'Fiscal PDF Ready',
                            });

                            // Optional: Open URL in new tab or show modal
                            // window.open(payload.pdf_url, '_blank');
                        }
                    }
                });
            });
        },
    };

    registry.category("services").add("fiscal_pdf_bus_listener", FiscalPDFBusListener);
});
