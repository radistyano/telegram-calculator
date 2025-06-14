from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, scoped_session
from sqlalchemy.pool import QueuePool
import datetime

Base = declarative_base()

class Rate(Base):
    """Model for storing USDT buy/sell rates"""
    __tablename__ = 'rates'
    
    id = Column(Integer, primary_key=True)
    type = Column(String(10), nullable=False)  # 'buy' or 'sell'
    value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    def __repr__(self):
        return f"<Rate(type='{self.type}', value={self.value})>"


class FeeRange(Base):
    """Model for storing fee ranges and amounts"""
    __tablename__ = 'fee_ranges'
    
    id = Column(Integer, primary_key=True)
    min_amount = Column(Float, nullable=False)
    max_amount = Column(Float, nullable=True)  # NULL means unlimited
    fee_amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    def __repr__(self):
        max_str = f"{self.max_amount}" if self.max_amount is not None else "unlimited"
        return f"<FeeRange(min={self.min_amount}, max={max_str}, fee={self.fee_amount})>"


class Transaction(Base):
    """Model for storing transaction history and profit statistics"""
    __tablename__ = 'transactions'
    
    id = Column(Integer, primary_key=True)
    type = Column(String(10), nullable=False)  # 'buy' or 'sell'
    usdt_amount = Column(Float, nullable=False)
    rate = Column(Float, nullable=False)
    fee = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    profit = Column(Float, nullable=True)  # NULL for buy transactions
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    def __repr__(self):
        return f"<Transaction(type='{self.type}', usdt={self.usdt_amount}, total={self.total_amount})>"


class CustomFormula(Base):
    """Model for storing custom calculation formulas"""
    __tablename__ = 'custom_formulas'
    
    id = Column(Integer, primary_key=True)
    type = Column(String(10), nullable=False)  # 'buy' or 'sell'
    formula = Column(Text, nullable=False)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    def __repr__(self):
        return f"<CustomFormula(type='{self.type}', formula='{self.formula}', active={self.is_active})>"


# Database initialization
def init_db(db_path="sqlite:///usdt_calculator.db"):
    """Initialize the database and create tables if they don't exist"""
    engine = create_engine(
        db_path,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return scoped_session(session_factory)


# Default data initialization
def init_default_data(session):
    """Initialize default data if tables are empty"""
    # Check if rates exist
    if session.query(Rate).count() == 0:
        # Add default buy rate
        buy_rate = Rate(type='buy', value=16400.0)
        session.add(buy_rate)
        
        # Add default sell rate
        sell_rate = Rate(type='sell', value=16100.0)
        session.add(sell_rate)
    
    # Check if fee ranges exist
    if session.query(FeeRange).count() == 0:
        # Add default fee ranges
        fee_ranges = [
            FeeRange(min_amount=0, max_amount=25000, fee_amount=3000),
            FeeRange(min_amount=26000, max_amount=100000, fee_amount=5000),
            FeeRange(min_amount=101000, max_amount=150000, fee_amount=6000),
            FeeRange(min_amount=151000, max_amount=200000, fee_amount=7000),
            FeeRange(min_amount=201000, max_amount=500000, fee_amount=10000),
            FeeRange(min_amount=501000, max_amount=5000000, fee_amount=17000),
            FeeRange(min_amount=5001000, max_amount=None, fee_amount=25000),
        ]
        session.add_all(fee_ranges)
    
    # Check if custom formulas exist
    if session.query(CustomFormula).count() == 0:
        # Add default formulas
        buy_formula = CustomFormula(type='buy', formula="{usdt_amount} * {rate} + {fee}", is_active=True)
        sell_formula = CustomFormula(type='sell', formula="{usdt_amount} * {rate} - {fee}", is_active=True)
        session.add_all([buy_formula, sell_formula])
    
    session.commit() 