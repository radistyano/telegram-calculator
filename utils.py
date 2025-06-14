from models import Rate, FeeRange, Transaction, CustomFormula
from sqlalchemy import func
import re
import logging
import asyncio
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Dict, Optional

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cache duration in seconds
CACHE_DURATION = 300  # 5 minutes

# Cache for rates
_rate_cache: Dict[str, Dict] = {}
_rate_cache_time: Dict[str, datetime] = {}

@lru_cache(maxsize=128)
def get_rate(session, rate_type):
    """Get the current rate for buy or sell with caching"""
    current_time = datetime.utcnow()
    
    # Check cache
    if rate_type in _rate_cache and rate_type in _rate_cache_time:
        if current_time - _rate_cache_time[rate_type] < timedelta(seconds=CACHE_DURATION):
            return _rate_cache[rate_type]
    
    # Get from database
    rate = session.query(Rate).filter(Rate.type == rate_type).first()
    if not rate:
        logger.error(f"No {rate_type} rate found in database")
        return None
    
    result = {
        'value': rate.value,
        'updated_at': rate.updated_at
    }
    
    # Update cache
    _rate_cache[rate_type] = result
    _rate_cache_time[rate_type] = current_time
    
    return result


def update_rate(db_session, rate_type, value):
    """
    Update rate in database
    
    Args:
        db_session: Database session
        rate_type: 'buy' or 'sell'
        value: New rate value
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        rate = db_session.query(Rate).filter(Rate.type == rate_type).first()
        if rate:
            rate.value = value
            rate.updated_at = datetime.utcnow()
        else:
            rate = Rate(type=rate_type, value=value)
            db_session.add(rate)
        
        db_session.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating rate: {e}")
        db_session.rollback()
        return False


@lru_cache(maxsize=128)
def get_fee_for_amount(session, amount):
    """Get the fee amount for a given transaction amount with caching"""
    try:
        # Find the fee range that contains the amount
        fee_range = session.query(FeeRange).filter(
            FeeRange.min_amount <= amount,
            (FeeRange.max_amount >= amount) | (FeeRange.max_amount == None)
        ).first()
        
        if not fee_range:
            logger.error(f"No fee range found for amount {amount}")
            return 0
        
        return fee_range.fee_amount
    except Exception as e:
        logger.error(f"Error getting fee for amount {amount}: {e}")
        return 0


def get_all_fee_ranges(session):
    """Get all fee ranges"""
    return session.query(FeeRange).order_by(FeeRange.min_amount).all()


def add_fee_range(session, min_amount, max_amount, fee_amount):
    """Add a new fee range"""
    try:
        # Check if the range overlaps with existing ranges
        overlapping = session.query(FeeRange).filter(
            ((FeeRange.min_amount <= min_amount) & ((FeeRange.max_amount >= min_amount) | (FeeRange.max_amount == None))) |
            ((FeeRange.min_amount <= max_amount) & ((FeeRange.max_amount >= max_amount) | (FeeRange.max_amount == None))) |
            ((FeeRange.min_amount >= min_amount) & ((FeeRange.max_amount <= max_amount) | (FeeRange.max_amount == None)))
        ).first()
        
        if overlapping:
            logger.error(f"Fee range overlaps with existing range: {overlapping}")
            return False
        
        fee_range = FeeRange(min_amount=min_amount, max_amount=max_amount, fee_amount=fee_amount)
        session.add(fee_range)
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding fee range: {e}")
        session.rollback()
        return False


def update_fee_range(session, fee_id, min_amount, max_amount, fee_amount):
    """Update an existing fee range"""
    try:
        fee_range = session.query(FeeRange).filter(FeeRange.id == fee_id).first()
        if not fee_range:
            logger.error(f"Fee range with ID {fee_id} not found")
            return False
        
        # Check if the updated range overlaps with other ranges
        overlapping = session.query(FeeRange).filter(
            FeeRange.id != fee_id,
            ((FeeRange.min_amount <= min_amount) & ((FeeRange.max_amount >= min_amount) | (FeeRange.max_amount == None))) |
            ((FeeRange.min_amount <= max_amount) & ((FeeRange.max_amount >= max_amount) | (FeeRange.max_amount == None))) |
            ((FeeRange.min_amount >= min_amount) & ((FeeRange.max_amount <= max_amount) | (FeeRange.max_amount == None)))
        ).first()
        
        if overlapping:
            logger.error(f"Updated fee range overlaps with existing range: {overlapping}")
            return False
        
        fee_range.min_amount = min_amount
        fee_range.max_amount = max_amount
        fee_range.fee_amount = fee_amount
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating fee range: {e}")
        session.rollback()
        return False


def delete_fee_range(session, fee_id):
    """Delete a fee range"""
    try:
        fee_range = session.query(FeeRange).filter(FeeRange.id == fee_id).first()
        if not fee_range:
            logger.error(f"Fee range with ID {fee_id} not found")
            return False
        
        session.delete(fee_range)
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error deleting fee range: {e}")
        session.rollback()
        return False


def calculate_transaction(db_session, amount, transaction_type):
    """
    Calculate transaction details
    
    Args:
        db_session: Database session
        amount: Amount in USDT
        transaction_type: 'buy' or 'sell'
        
    Returns:
        dict: Transaction details
    """
    try:
        # Get the rate
        rate = get_rate(db_session, transaction_type)
        if not rate:
            return None
        
        # Calculate IDR amount
        idr_amount = amount * rate['value']
        
        # Get the fee
        fee = get_fee_for_amount(db_session, idr_amount)
        
        # Calculate total amount
        if transaction_type == 'buy':
            total_amount = idr_amount + fee
        else:  # sell
            total_amount = idr_amount - fee
        
        # Record the transaction
        transaction = Transaction(
            type=transaction_type,
            usdt_amount=amount,
            idr_amount=idr_amount,
            rate=rate['value'],
            fee=fee,
            total_amount=total_amount
        )
        db_session.add(transaction)
        db_session.commit()
        
        return {
            'usdt_amount': amount,
            'idr_amount': idr_amount,
            'rate': rate['value'],
            'fee': fee,
            'total_amount': total_amount,
            'updated_at': rate['updated_at']
        }
    except Exception as e:
        logger.error(f"Error calculating transaction: {e}")
        db_session.rollback()
        return None


def get_profit_statistics(session):
    """Get profit statistics"""
    try:
        # Get total profit from sell transactions
        total_profit = session.query(Transaction).filter(
            Transaction.type == 'sell',
            Transaction.profit != None
        ).with_entities(func.sum(Transaction.profit)).scalar() or 0
        
        # Get total transactions
        total_transactions = session.query(Transaction).count()
        
        # Get total buy and sell transactions
        total_buy = session.query(Transaction).filter(Transaction.type == 'buy').count()
        total_sell = session.query(Transaction).filter(Transaction.type == 'sell').count()
        
        # Get total USDT bought and sold
        total_usdt_bought = session.query(Transaction).filter(
            Transaction.type == 'buy'
        ).with_entities(func.sum(Transaction.usdt_amount)).scalar() or 0
        
        total_usdt_sold = session.query(Transaction).filter(
            Transaction.type == 'sell'
        ).with_entities(func.sum(Transaction.usdt_amount)).scalar() or 0
        
        return {
            'total_profit': total_profit,
            'total_transactions': total_transactions,
            'total_buy': total_buy,
            'total_sell': total_sell,
            'total_usdt_bought': total_usdt_bought,
            'total_usdt_sold': total_usdt_sold
        }
    except Exception as e:
        logger.error(f"Error getting profit statistics: {e}")
        return None


def update_custom_formula(session, formula_type, formula_str):
    """Update the custom formula for buy or sell"""
    try:
        # Validate the formula
        if not is_valid_formula(formula_str):
            logger.error(f"Invalid formula: {formula_str}")
            return False
        
        # Deactivate all formulas of this type
        session.query(CustomFormula).filter(
            CustomFormula.type == formula_type
        ).update({CustomFormula.is_active: False})
        
        # Check if formula already exists
        formula = session.query(CustomFormula).filter(
            CustomFormula.type == formula_type,
            CustomFormula.formula == formula_str
        ).first()
        
        if formula:
            # Activate the existing formula
            formula.is_active = True
        else:
            # Create a new formula
            formula = CustomFormula(type=formula_type, formula=formula_str, is_active=True)
            session.add(formula)
        
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating custom formula: {e}")
        session.rollback()
        return False


def is_valid_formula(formula_str):
    """Validate a custom formula"""
    try:
        # Check if the formula contains the required variables
        if not all(var in formula_str for var in ['{usdt_amount}', '{rate}', '{fee}']):
            return False
        
        # Check if the formula is syntactically valid
        test_formula = formula_str
        test_formula = test_formula.replace('{usdt_amount}', '10')
        test_formula = test_formula.replace('{rate}', '16000')
        test_formula = test_formula.replace('{fee}', '5000')
        
        # Try to evaluate the formula
        eval(test_formula)
        return True
    except Exception as e:
        logger.error(f"Invalid formula: {e}")
        return False


def get_active_formula(session, formula_type):
    """Get the active formula for buy or sell"""
    return session.query(CustomFormula).filter(
        CustomFormula.type == formula_type,
        CustomFormula.is_active == True
    ).first()


async def calculate_transaction_async(db_session, amount, transaction_type):
    """
    Asynchronous wrapper for calculate_transaction
    
    Args:
        db_session: Database session
        amount: Amount in USDT
        transaction_type: 'buy' or 'sell'
        
    Returns:
        dict: Transaction details
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, calculate_transaction, db_session, amount, transaction_type)


def clear_rate_cache():
    """Clear the rate cache"""
    _rate_cache.clear()
    _rate_cache_time.clear()
    get_rate.cache_clear()
    get_fee_for_amount.cache_clear() 