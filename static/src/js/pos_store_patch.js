/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    getZimraQrCode(order) {
        if (!order.zimra_qr_code) {
            return null;
        }
        const encoded = encodeURIComponent(order.zimra_qr_code);
        return `/report/barcode/QR/${encoded}?width=150&height=150`;
    },
});
