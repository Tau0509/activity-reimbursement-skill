#!/usr/bin/env python3
"""Reconcile declared RMB amounts; this is not a reimbursement approval check."""
import argparse
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


def amount(value, label):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f'{label}: amount must not be boolean')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label}: invalid amount {value!r}') from None
    if not result.is_finite() or result < 0 or result != result.quantize(Decimal('0.01')):
        raise ValueError(f'{label}: use a nonnegative amount with at most two decimals')
    return result


def show(value):
    return None if value is None else f'{value:.2f}'


def reconcile(data):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('items must be a list')
    if not data['items']:
        raise ValueError('items must not be empty')
    rows, issues = [], []
    invoice_ids, transaction_ids = defaultdict(list), defaultdict(list)
    item_ids = set()
    totals = {key: Decimal('0.00') for key in ('invoice', 'gross_payment', 'verified_refund', 'net_payment')}
    complete = {key: True for key in totals}
    for item in data['items']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].strip():
            raise ValueError('each item needs a nonempty string id')
        iid = item['id']
        if iid in item_ids:
            raise ValueError(f'duplicate item id: {iid}')
        item_ids.add(iid)
        invoice = amount(item.get('invoice_amount'), f'{iid}.invoice_amount')
        if invoice is None:
            issues.append({'item': iid, 'type': 'missing_invoice_amount'})
        invoices = item.get('invoices', [])
        if not isinstance(invoices, list):
            raise ValueError(f'{iid}.invoices must be a list')
        if not invoices:
            issues.append({'item': iid, 'type': 'missing_invoice_reference'})
        for inv in invoices:
            if not isinstance(inv, dict):
                raise ValueError(f'{iid}: invalid invoice reference')
            if inv.get('number'):
                invoice_ids[str(inv['number'])].append(iid)
            if not inv.get('source'):
                issues.append({'item': iid, 'type': 'missing_invoice_source'})
        if not item.get('orders'):
            issues.append({'item': iid, 'type': 'missing_order_reference'})
        payments = item.get('payments')
        if payments is not None and not isinstance(payments, list):
            raise ValueError(f'{iid}.payments must be a list')
        gross = Decimal('0.00') if payments else None
        if not payments:
            issues.append({'item': iid, 'type': 'missing_payment_records'})
        for event in payments or []:
            if not isinstance(event, dict):
                raise ValueError(f'{iid}: invalid payment')
            value = amount(event.get('amount'), f'{iid}.payments.amount')
            if value is None:
                gross = None
                issues.append({'item': iid, 'type': 'missing_payment_amount'})
            elif gross is not None:
                gross += value
            if not event.get('source'):
                issues.append({'item': iid, 'type': 'missing_payment_source'})
            if event.get('transaction_id'):
                transaction_ids[str(event['transaction_id'])].append(f'{iid}:payment')
        refunds = item.get('refunds', [])
        if not isinstance(refunds, list):
            raise ValueError(f'{iid}.refunds must be a list')
        refunded = Decimal('0.00')
        for event in refunds:
            if not isinstance(event, dict):
                raise ValueError(f'{iid}: invalid refund')
            value = amount(event.get('amount'), f'{iid}.refunds.amount')
            if event.get('verified') is not True or not event.get('source') or value is None:
                issues.append({'item': iid, 'type': 'unverified_refund_not_deducted'})
            else:
                refunded += value
            if event.get('transaction_id'):
                transaction_ids[str(event['transaction_id'])].append(f'{iid}:refund')
        if gross is not None and refunded > gross:
            raise ValueError(f'{iid}: verified refunds exceed recorded payments')
        net = None if gross is None else gross - refunded
        diff = None if invoice is None or net is None else invoice - net
        if diff is not None and diff != 0:
            issues.append({'item': iid, 'type': 'invoice_payment_difference', 'amount': show(diff)})
        values = dict(invoice=invoice, gross_payment=gross, verified_refund=refunded, net_payment=net)
        for key, value in values.items():
            if value is None:
                complete[key] = False
            else:
                totals[key] += value
        rows.append({'id': iid, 'name': item.get('name', ''), **{k: show(v) for k, v in values.items()}, 'difference': show(diff)})
    for kind, refs in [('invoice_number', invoice_ids), ('transaction_id', transaction_ids)]:
        for key, locations in refs.items():
            if len(locations) > 1:
                issues.append({'type': f'duplicate_{kind}', 'value': key, 'locations': locations})
    sums = {key: show(value) if complete[key] else None for key, value in totals.items()}
    sums['difference'] = show(totals['invoice'] - totals['net_payment']) if complete['invoice'] and complete['net_payment'] else None
    for field, key in [('expected_invoice_total', 'invoice'), ('expected_net_payment_total', 'net_payment')]:
        if field in data:
            expected = amount(data[field], field)
            if expected is None:
                raise ValueError(f'{field} must be an amount when supplied')
            if not complete[key]:
                issues.append({'type': f'{field}_not_checkable_missing_data'})
            elif totals[key] != expected:
                issues.append({'type': f'{field}_mismatch', 'expected': show(expected), 'calculated': show(totals[key])})
    return {'items': rows, 'totals': sums, 'arithmetic_complete': all(complete.values()), 'issues': issues,
            'scope': 'Recorded amounts only; no determination of evidence authenticity or reimbursability.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = reconcile(json.loads(args.input.read_text(encoding='utf-8-sig')))
        status = 0
    except (OSError, ValueError, TypeError, InvalidOperation) as exc:
        result, status = {'error': str(exc)}, 2
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text, end='')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
