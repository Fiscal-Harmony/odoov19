import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

const FH_LOADING_BUTTONS = ["action_fiscalize_invoice", "action_retry_fiscalization"];

patch(FormController.prototype, {
    async beforeExecuteActionButton(clickParams) {
        const result = await super.beforeExecuteActionButton(...arguments);
        if (result !== false && FH_LOADING_BUTTONS.includes(clickParams.name)) {
            this.ui.block({ message: "Fiscalising with ZIMRA, please wait..." });
            this._fhLoading = true;
        }
        return result;
    },

    async afterExecuteActionButton(clickParams) {
        if (this._fhLoading) {
            this._fhLoading = false;
            this.ui.unblock();
        }
        return super.afterExecuteActionButton(...arguments);
    },
});