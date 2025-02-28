from functools import partial
from typing import Callable, Dict, Optional, Tuple, Union

import torch
from torch import nn
from torch.optim.lr_scheduler import ReduceLROnPlateau

from collie.model.base import BasePipeline, INTERACTIONS_LIKE_INPUT, ScaledEmbedding, ZeroEmbedding
from collie.utils import get_init_arguments, merge_docstrings


class MatrixFactorizationModel(BasePipeline):
    # NOTE: the full docstring is merged in with ``BasePipeline``'s using ``merge_docstrings``.
    # Only the description of new or changed parameters are included in this docstring
    """
    Training pipeline for the matrix factorization model.

    ``MatrixFactorizationModel`` models have an embedding layer for both users and items which are
    dot-producted together to output a single float ranking value.

    Collie adds a twist on to this incredibly popular framework by allowing separate optimizers
    for embeddings and bias terms. With larger datasets and multiple epochs of training, a model
    might incorrectly learn to only optimize the bias terms for a quicker path towards a local
    loss minimum, essentially memorizing how popular each item is. By using a separate, slower
    optimizer for the bias terms (like Stochastic Gradient Descent), the model must prioritize
    optimizing the embeddings for meaningful, more varied recommendations, leading to a model
    that is able to achieve a much lower loss. See the documentation below for ``bias_lr`` and
    ``bias_optimizer`` input arguments for implementation details.

    All ``MatrixFactorizationModel`` instances are subclasses of the ``LightningModule`` class
    provided by PyTorch Lightning. This means to train a model, you will need a
    ``collie.model.CollieTrainer`` object, but the model can be saved and loaded without this
    ``Trainer`` instance. Example usage may look like:

    .. code-block:: python

        from collie.model import CollieTrainer, MatrixFactorizationModel


        model = MatrixFactorizationModel(train=train)
        trainer = CollieTrainer(model)
        trainer.fit(model)
        model.eval()

        # do evaluation as normal with ``model``

        model.save_model(filename='model.pth')
        new_model = MatrixFactorizationModel(load_model_path='model.pth')

        # do evaluation as normal with ``new_model``

    Parameters
    ----------
    embedding_dim: int
        Number of latent factors to use for user and item embeddings
    dropout_p: float
        Probability of dropout
    sparse: bool
        Whether or not to treat embeddings as sparse tensors. If ``True``, cannot use weight decay
        on the optimizer
    bias_lr: float
        Bias terms learning rate. If 'infer', will set equal to ``lr``
    bias_optimizer: torch.optim or str
        Optimizer for the bias terms. This supports the same string options as ``optimizer``, with
        the addition of ``infer``, which will set the optimizer equal to ``optimizer``. If
        ``bias_optimizer`` is ``None``, only a single optimizer will be created for all model
        parameters
    y_range: tuple
        Specify as ``(min, max)`` to apply a sigmoid layer to the output score of the model to get
        predicted ratings within the range of ``min`` and ``max``

    """
    def __init__(self,
                 train: INTERACTIONS_LIKE_INPUT = None,
                 val: INTERACTIONS_LIKE_INPUT = None,
                 embedding_dim: int = 30,
                 dropout_p: float = 0.0,
                 sparse: bool = False,
                 lr: float = 1e-3,
                 bias_lr: Optional[Union[float, str]] = 1e-2,
                 lr_scheduler_func: Optional[Callable] = partial(ReduceLROnPlateau,
                                                                 patience=1,
                                                                 verbose=True),
                 weight_decay: float = 0.0,
                 optimizer: Union[str, Callable] = 'adam',
                 bias_optimizer: Optional[Union[str, Callable]] = 'sgd',
                 loss: Union[str, Callable] = 'hinge',
                 metadata_for_loss: Optional[Dict[str, torch.tensor]] = None,
                 metadata_for_loss_weights: Optional[Dict[str, float]] = None,
                 y_range: Optional[Tuple[float, float]] = None,
                 load_model_path: Optional[str] = None,
                 map_location: Optional[str] = None):
        super().__init__(**get_init_arguments())

    __doc__ = merge_docstrings(BasePipeline, __doc__, __init__)

    def _setup_model(self, **kwargs) -> None:
        """
        Method for building model internals that rely on the data passed in.

        This method will be called after ``prepare_data``.

        """
        self.user_biases = ZeroEmbedding(num_embeddings=self.hparams.num_users,
                                         embedding_dim=1,
                                         sparse=self.hparams.sparse)
        self.item_biases = ZeroEmbedding(num_embeddings=self.hparams.num_items,
                                         embedding_dim=1,
                                         sparse=self.hparams.sparse)
        self.user_embeddings = ScaledEmbedding(num_embeddings=self.hparams.num_users,
                                               embedding_dim=self.hparams.embedding_dim,
                                               sparse=self.hparams.sparse)
        self.item_embeddings = ScaledEmbedding(num_embeddings=self.hparams.num_items,
                                               embedding_dim=self.hparams.embedding_dim,
                                               sparse=self.hparams.sparse)
        self.dropout = nn.Dropout(p=self.hparams.dropout_p)

    def forward(self, users: torch.tensor, items: torch.tensor) -> torch.tensor:
        """
        Forward pass through the model.

        Simple matrix factorization for a single user and item looks like:

        ````prediction = (user_embedding * item_embedding) + user_bias + item_bias````

        If dropout is added, it is applied to the two embeddings and not the biases.

        Parameters
        ----------
        users: tensor, 1-d
            Array of user indices
        items: tensor, 1-d
            Array of item indices

        Returns
        -------
        preds: tensor, 1-d
            Predicted ratings or rankings

        """
        user_embeddings = self.user_embeddings(users)
        item_embeddings = self.item_embeddings(items)

        preds = (
            torch.mul(self.dropout(user_embeddings), self.dropout(item_embeddings)).sum(axis=1)
            + self.user_biases(users).squeeze(1)
            + self.item_biases(items).squeeze(1)
        )

        if self.hparams.y_range is not None:
            preds = (
                torch.sigmoid(preds)
                * (self.hparams.y_range[1] - self.hparams.y_range[0])
                + self.hparams.y_range[0]
            )

        return preds

    def _get_item_embeddings(self) -> torch.tensor:
        """Get item embeddings on device."""
        return self.item_embeddings.weight.data
