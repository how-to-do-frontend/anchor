
from sqlalchemy.orm import sessionmaker, scoped_session, Session
from sqlalchemy     import create_engine

from typing import Optional, Generator, List

from .objects import (
    DBRelationship,
    DBChannel,
    DBStats,
    DBUser,
    Base
)

import traceback
import bancho

class Postgres:
    def __init__(self, username: str, password: str, host: str, port: int) -> None:
        self.engine = create_engine(
            f'postgresql://{username}:{password}@{host}:{port}/{username}', 
            echo=False
        )

        Base.metadata.create_all(bind=self.engine)

        self.session_factory = scoped_session(
            sessionmaker(self.engine, expire_on_commit=False, autoflush=True)
        )
    
    @property
    def session(self) -> Session:
        for session in self.create_session():
            return session

    def create_session(self) -> Generator:
        session = self.session_factory()
        try:
            yield session
        except Exception as e:
            traceback.print_exc()
            bancho.services.logger.critical(f'Transaction failed: "{e}". Performing rollback...')
            session.rollback()
        finally:
            session.close()

    def user_by_name(self, name: str) -> Optional[DBUser]:
        return self.session.query(DBUser).filter(DBUser.name == name).first()
    
    def user_by_id(self, id: int) -> Optional[DBUser]:
        return self.session.query(DBUser).filter(DBUser.id == id).first()
    
    def channels(self) -> List[DBChannel]:
        return self.session.query(DBChannel).all()
    
    def stats(self, user_id: int, mode: int) -> Optional[DBStats]:
        return self.session.query(DBStats).filter(DBStats.user_id == user_id).filter(DBStats.mode == mode).first()
    
    def relationships(self, user_id: int) -> List[DBStats]:
        return self.session.query(DBRelationship).filter(DBRelationship.user_id == user_id).all()
    
    def add_relationship(self, user_id: int, target_id: int, friend: bool = True) -> DBRelationship:
        instance = self.session
        instance.add(
            rel := DBRelationship(
                user_id,
                target_id,
                int(not friend)
            )
        )
        instance.commit()

        return rel
    
    def remove_relationship(self, user_id: int, target_id: int, status: int = 0):
        instance = self.session
        rel = instance.query(DBRelationship).filter(DBRelationship.user_id == user_id).filter(DBRelationship.target_id == target_id).filter(DBRelationship.status == status)

        if rel.first():
            rel.delete()
            instance.commit()
