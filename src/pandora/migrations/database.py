# -*- coding: utf-8 -*-

from sqlalchemy import create_engine, NullPool
from sqlalchemy.orm import sessionmaker

from ..exts.config import DATABASE_URI

engine = create_engine(DATABASE_URI, echo=False, poolclass=NullPool)

Session = sessionmaker(bind=engine)

session = Session()
