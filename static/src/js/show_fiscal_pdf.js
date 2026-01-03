/** @odoo-module */

import { Dialog } from "@web/core/dialog/dialog";
import { Component, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";

class FiscalPdfViewer extends Component {
    setup() {
        onWillStart(() => {
            this.src = `data:application/pdf;base64,${this.props.pdf_data}`;
        });
    }

    printPdf() {
        const iframe = this.el.querySelector("iframe");
        iframe?.contentWindow?.print();
    }
}

FiscalPdfViewer.template = "your_module.FiscalPdfViewer";
FiscalPdfViewer.props = {
    pdf_data: String,
};

registry.category("actions").add("show_fiscal_pdf", (env, { pdf_data, title }) => {
    const dialog = new Dialog(env, {
        title,
        body: env.owly.create(FiscalPdfViewer, { pdf_data }),
        buttons: [
            {
                text: "Print",
                classes: "btn-primary",
                click: () => dialog.body.el.querySelector("iframe").contentWindow.print(),
            },
            {
                text: "Close",
                close: true,
            },
        ],
    });
    dialog.open();
});
