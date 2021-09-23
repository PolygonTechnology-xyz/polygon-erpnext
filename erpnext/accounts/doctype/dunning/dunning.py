# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import json

import frappe
from frappe.utils import getdate

from erpnext.accounts.general_ledger import make_gl_entries, make_reverse_gl_entries
from erpnext.controllers.accounts_controller import AccountsController


class Dunning(AccountsController):

	def validate(self):
		self.validate_overdue_payments()
		self.validate_totals()

		if not self.income_account:
			self.income_account = frappe.db.get_value("Company", self.company, "default_income_account")

	def validate_overdue_payments(self):
		daily_interest = self.rate_of_interest / 100 / 365

		for row in self.overdue_payments:
			row.overdue_days = (getdate(self.posting_date) - getdate(row.due_date)).days or 0
			row.interest = row.outstanding * daily_interest * row.overdue_days

	def validate_totals(self):
		self.total_outstanding = sum(row.outstanding for row in self.overdue_payments)
		self.total_interest = sum(row.interest for row in self.overdue_payments)
		self.dunning_amount = self.total_interest + self.dunning_fee
		self.grand_total = self.total_outstanding + self.dunning_amount

	def on_submit(self):
		self.make_gl_entries()

	def on_cancel(self):
		if self.dunning_amount:
			self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Payment Ledger Entry")
			make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)

	def make_gl_entries(self):
		if not self.dunning_amount:
			return

		cost_center = self.cost_center or frappe.get_cached_value("Company",  self.company,  "cost_center")

		make_gl_entries(
			[
				self.get_gl_dict({
					"account": self.debit_to,
					"party_type": "Customer",
					"party": self.customer,
					"due_date": self.due_date,
					"against": self.income_account,
					"debit": self.dunning_amount,
					"debit_in_account_currency": self.dunning_amount,
					"against_voucher": self.name,
					"against_voucher_type": "Dunning",
					"cost_center": cost_center
				}),
				self.get_gl_dict({
					"account": self.income_account,
					"against": self.customer,
					"credit": self.dunning_amount,
					"cost_center": cost_center,
					"credit_in_account_currency": self.dunning_amount
				})
			],
			cancel=(self.docstatus == 2),
			update_outstanding="No",
			merge_entries=False
		)


def resolve_dunning(doc, state):
	for reference in doc.references:
		if reference.reference_doctype == "Sales Invoice" and reference.outstanding_amount <= 0:
			dunnings = frappe.get_list(
				"Dunning",
				filters={"sales_invoice": reference.reference_name, "status": ("!=", "Resolved")},
				ignore_permissions=True,
			)

			for dunning in dunnings:
				frappe.db.set_value("Dunning", dunning.name, "status", "Resolved")



@frappe.whitelist()
def get_dunning_letter_text(dunning_type, doc, language=None):
	if isinstance(doc, str):
		doc = json.loads(doc)
	if language:
		filters = {"parent": dunning_type, "language": language}
	else:
		filters = {"parent": dunning_type, "is_default_language": 1}
	letter_text = frappe.db.get_value(
		"Dunning Letter Text", filters, ["body_text", "closing_text", "language"], as_dict=1
	)
	if letter_text:
		return {
			"body_text": frappe.render_template(letter_text.body_text, doc),
			"closing_text": frappe.render_template(letter_text.closing_text, doc),
			"language": letter_text.language,
		}
