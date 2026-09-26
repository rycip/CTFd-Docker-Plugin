from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from CTFd.models import Challenges, db


class ContainerChallengeModel(Challenges):
    __mapper_args__ = {"polymorphic_identity": "container"}
    id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True
    )
    image = db.Column(db.Text)
    ports = db.Column(db.Text)
    command = db.Column(db.Text, default="")
    volumes = db.Column(db.Text, default="")

    # Dynamic challenge properties
    initial = db.Column(db.Integer, default=0)
    minimum = db.Column(db.Integer, default=0)
    decay = db.Column(db.Integer, default=0)

    def __init__(self, *args, **kwargs):
        super(ContainerChallengeModel, self).__init__(**kwargs)
        self.value = kwargs["initial"]


class ContainerInfoModel(db.Model):
    __mapper_args__ = {"polymorphic_identity": "container_info"}
    container_id = db.Column(db.String(512), primary_key=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE")
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    timestamp = db.Column(db.Integer)
    expires = db.Column(db.Integer)
    team = relationship("Users", foreign_keys=[user_id])
    challenge = relationship(ContainerChallengeModel, foreign_keys=[challenge_id])
    ports = relationship(
        "ContainerInfoModelPort",
        back_populates="container",
        cascade="all, delete-orphan",
    )


class ContainerInfoModelPort(db.Model):
    container_id = db.Column(
        db.String(512),
        db.ForeignKey("container_info_model.container_id"),
        primary_key=True,
    )
    port = db.Column(db.Integer, primary_key=True)
    container = relationship(ContainerInfoModel, back_populates="ports")


class ContainerSettingsModel(db.Model):
    __mapper_args__ = {"polymorphic_identity": "container_settings"}
    key = db.Column(db.String(512), primary_key=True)
    value = db.Column(db.Text)
