from models import Rate, FeeRange, Transaction, CustomFormula
from sqlalchemy import func
import re
import logging

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def get_rate(session, rate_type):
    """Get the current rate for buy or sell"""
    rate = session.query(Rate).filter(Rate.type == rate_type).first()
    if not rate:
        logger.error(f"No {rate_type} rate found in database")
        return None
    return rate.value


def update_rate(session, rate_type, new_value):
    """Update the rate for buy or sell"""
    try:
        rate = session.query(Rate).filter(Rate.type == rate_type).first()
        if not rate:
            # Create new rate if it doesn't exist
            rate = Rate(type=rate_type, value=new_value)
            session.add(rate)
        else:
            rate.value = new_value
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating {rate_type} rate: {e}")
        session.rollback()
        return False


def get_fee_for_amount(session, amount):
    """Get the fee amount for a given transaction amount"""
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


def calculate_transaction(session, usdt_amount, transaction_type):
    """Calculate the total amount for a transaction"""
    try:
        # Get the rate
        rate = get_rate(session, transaction_type)
        if not rate:
            return None
        
        # Calculate the base amount
        base_amount = usdt_amount * rate
        
        # Get the fee
        fee = get_fee_for_amount(session, base_amount)
        
        # Get the custom formula if available
        formula = session.query(CustomFormula).filter(
            CustomFormula.type == transaction_type,
            CustomFormula.is_active == True
        ).first()
        
        if formula:
            # Use the custom formula
            try:
                # Replace variables in the formula
                formula_str = formula.formula
                formula_str = formula_str.replace("{usdt_amount}", str(usdt_amount))
                formula_str = formula_str.replace("{rate}", str(rate))
                formula_str = formula_str.replace("{fee}", str(fee))
                
                # Evaluate the formula
                total_amount = eval(formula_str)
            except Exception as e:
                logger.error(f"Error evaluating custom formula: {e}")
                # Fall back to default formula
                if transaction_type == 'buy':
                    total_amount = base_amount + fee
                else:  # sell
                    total_amount = base_amount - fee
        else:
            # Use default formula
            if transaction_type == 'buy':
                total_amount = base_amount + fee
            else:  # sell
                total_amount = base_amount - fee
        
        # Calculate profit for sell transactions
        profit = None
        if transaction_type == 'sell':
            buy_rate = get_rate(session, 'buy')
            if buy_rate:
                profit = (buy_rate - rate) * usdt_amount + fee
        
        # Record the transaction
        transaction = Transaction(
            type=transaction_type,
            usdt_amount=usdt_amount,
            rate=rate,
            fee=fee,
            total_amount=total_amount,
            profit=profit
        )
        session.add(transaction)
        session.commit()
        
        return {
            'usdt_amount': usdt_amount,
            'rate': rate,
            'fee': fee,
            'total_amount': total_amount,
            'profit': profit
        }
    except Exception as e:
        logger.error(f"Error calculating transaction: {e}")
        session.rollback()
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