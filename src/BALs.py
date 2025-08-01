import pandas as pd
import json
from typing import Dict, List as PyList

from ssz import Serializable
from ssz.sedes import ByteVector, ByteList, uint16, uint64, List as SSZList

if not hasattr(ByteList, 'length'):
    ByteList.length = property(lambda self: self.max_length)

MAX_TXS = 30_000
MAX_SLOTS = 300_000
MAX_ACCOUNTS = 300_000
MAX_CODE_SIZE = 24_576

Address = ByteVector(20)
StorageKey = ByteVector(32)
StorageValue = ByteVector(32)
TxIndex = uint16
Balance = ByteVector(16)
Nonce = uint64
CodeData = ByteList(MAX_CODE_SIZE)

def parse_hex_or_zero(x):
    if pd.isna(x) or x is None:
        return 0
    return int(x, 16)

class StorageChange(Serializable):
    fields = [
        ('tx_index', TxIndex),
        ('new_value', StorageValue),
    ]

class BalanceChange(Serializable):
    fields = [
        ('tx_index', TxIndex),
        ('post_balance', Balance),
    ]

class NonceChange(Serializable):
    fields = [
        ('tx_index', TxIndex),
        ('new_nonce', Nonce),
    ]

class CodeChange(Serializable):
    fields = [
        ('tx_index', TxIndex),
        ('new_code', CodeData),
    ]

class StorageAccess(Serializable):
    fields = [
        ('slot', StorageKey),
        ('changes', SSZList(StorageChange, MAX_TXS)),
    ]

class AccountChanges(Serializable):
    fields = [
        ('address', Address),
        ('storage_writes', SSZList(StorageAccess, MAX_SLOTS)),
        ('storage_reads', SSZList(StorageKey, MAX_SLOTS)),
        ('balance_changes', SSZList(BalanceChange, MAX_TXS)),
        ('nonce_changes', SSZList(NonceChange, MAX_TXS)),
        ('code_changes', SSZList(CodeChange, MAX_TXS)),
    ]

class BlockAccessList(Serializable):
    fields = [
        ('account_changes', SSZList(AccountChanges, MAX_ACCOUNTS)),
    ]

class BALBuilder:
    """Builder for constructing BlockAccessList efficiently."""
    
    def __init__(self):
        self.accounts: Dict[bytes, Dict[str, PyList]] = {}
        
    def _ensure_account(self, address: bytes):
        """Ensure account exists in builder."""
        if address not in self.accounts:
            self.accounts[address] = {
                'storage_writes': {},
                'storage_reads': set(),
                'balance_changes': [],
                'nonce_changes': [],
                'code_changes': [],
            }
    
    def add_storage_write(self, address: bytes, slot: bytes, tx_index: int, new_value: bytes):
        self._ensure_account(address)
        
        if slot not in self.accounts[address]['storage_writes']:
            self.accounts[address]['storage_writes'][slot] = []
            
        change = StorageChange(tx_index=tx_index, new_value=new_value)
        self.accounts[address]['storage_writes'][slot].append(change)
    
    def add_storage_read(self, address: bytes, slot: bytes):
        self._ensure_account(address)
        self.accounts[address]['storage_reads'].add(slot)
    
    def add_balance_change(self, address: bytes, tx_index: int, post_balance: bytes):
        self._ensure_account(address)
        
        if len(post_balance) != 16:
            raise ValueError(f"Balance must be exactly 16 bytes, got {len(post_balance)}")
            
        change = BalanceChange(tx_index=tx_index, post_balance=post_balance)
        self.accounts[address]['balance_changes'].append(change)
    
    def add_nonce_change(self, address: bytes, tx_index: int, new_nonce: int):
        self._ensure_account(address)
        
        change = NonceChange(tx_index=tx_index, new_nonce=new_nonce)
        self.accounts[address]['nonce_changes'].append(change)
    
    def add_code_change(self, address: bytes, tx_index: int, new_code: bytes):
        self._ensure_account(address)
        
        change = CodeChange(tx_index=tx_index, new_code=new_code)
        self.accounts[address]['code_changes'].append(change)
    
    def add_touched_account(self, address: bytes):
        self._ensure_account(address)
    
    def build(self, ignore_reads: bool = False) -> BlockAccessList:
        account_changes_list = []
        
        for address, changes in self.accounts.items():
            storage_writes = []
            for slot, slot_changes in changes['storage_writes'].items():
                sorted_changes = sorted(slot_changes, key=lambda x: x.tx_index)
                storage_writes.append(StorageAccess(slot=slot, changes=sorted_changes))
            
            storage_reads = []
            if not ignore_reads:
                for slot in changes['storage_reads']:
                    if slot not in changes['storage_writes']:
                        storage_reads.append(slot)
            
            balance_changes = sorted(changes['balance_changes'], key=lambda x: x.tx_index)
            nonce_changes = sorted(changes['nonce_changes'], key=lambda x: x.tx_index)
            code_changes = sorted(changes['code_changes'], key=lambda x: x.tx_index)
            
            account_change = AccountChanges(
                address=address,
                storage_writes=storage_writes,
                storage_reads=storage_reads,
                balance_changes=balance_changes,
                nonce_changes=nonce_changes,
                code_changes=code_changes
            )
            
            account_changes_list.append(account_change)
        
        account_changes_list.sort(key=lambda x: bytes(x.address))
        
        return BlockAccessList(account_changes=account_changes_list)


def estimate_size_bytes(obj):
    return len(json.dumps(obj).encode('utf-8'))

def get_account_stats(bal: BlockAccessList) -> Dict[str, int]:
    stats = {
        'total_accounts': len(bal.account_changes),
        'accounts_with_storage_writes': 0,
        'accounts_with_storage_reads': 0,
        'accounts_with_balance_changes': 0,
        'accounts_with_nonce_changes': 0,
        'accounts_with_code_changes': 0,
        'total_storage_writes': 0,
        'total_storage_reads': 0,
        'total_balance_changes': 0,
        'total_nonce_changes': 0,
        'total_code_changes': 0,
    }
    
    for account in bal.account_changes:
        if account.storage_writes:
            stats['accounts_with_storage_writes'] += 1
            for storage_access in account.storage_writes:
                stats['total_storage_writes'] += len(storage_access.changes)
        
        if account.storage_reads:
            stats['accounts_with_storage_reads'] += 1
            stats['total_storage_reads'] += len(account.storage_reads)
            
        if account.balance_changes:
            stats['accounts_with_balance_changes'] += 1
            stats['total_balance_changes'] += len(account.balance_changes)
            
        if account.nonce_changes:
            stats['accounts_with_nonce_changes'] += 1
            stats['total_nonce_changes'] += len(account.nonce_changes)
            
        if account.code_changes:
            stats['accounts_with_code_changes'] += 1
            stats['total_code_changes'] += len(account.code_changes)
    
    return stats