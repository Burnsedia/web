# -*- coding: utf-8 -*-
"""Define the Avatar models.

Copyright (C) 2018 Gitcoin Core

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.

"""

import logging
from io import BytesIO
from secrets import token_hex
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.contrib.postgres.fields import JSONField
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import models
from django.utils.translation import gettext_lazy as _

from economy.models import SuperModel
from PIL import Image
from svgutils.compose import Figure, Line

from .utils import build_avatar_component, convert_img, convert_wand, dhash, get_temp_image_file, get_upload_filename

logger = logging.getLogger(__name__)


class BaseAvatar(SuperModel):
    """Store the options necessary to render a Gitcoin avatar."""

    ICON_SIZE = (215, 215)

    active = models.BooleanField(default=False)
    profile = models.ForeignKey(
        'dashboard.Profile', null=True, on_delete=models.CASCADE, related_name="%(app_label)s_%(class)s_related",
        blank=True
    )
    svg = models.FileField(
        upload_to=get_upload_filename, null=True, blank=True, help_text=_('The avatar SVG.')
    )
    png = models.ImageField(
        upload_to=get_upload_filename, null=True, blank=True, help_text=_('The avatar PNG.'),
    )
    hash = models.CharField(max_length=256)

    @property
    def avatar_url(self):
        """Return the appropriate avatar URL."""
        if self.png:
            return self.png.url
        if self.svg:
            return self.svg.url
        return ''

    def get_avatar_url(self):
        """Get the Avatar URL.

        """
        try:
            self.svg.url
        except ValueError:
            pass

        try:
            handle = self.profile.handle
        except Exception:
            handle = 'Self'

        return f'{settings.BASE_URL}dynamic/avatar/{handle}'

    @staticmethod
    def calculate_hash(image):
        return dhash(image)

    def find_similar(self):
        if self.hash:
            return BaseAvatar.objects.filter(profile=self.profile, hash=self.hash).last()

    def convert_field(self, source, input_fmt, output_fmt):
        """Handle converting from the source field to the target based on format."""
        try:
            # Convert the provided source to the specified output and store in BytesIO.
            if output_fmt == 'svg':
                tmpfile_io = convert_wand(source, input_fmt=input_fmt, output_fmt=output_fmt)
            else:
                tmpfile_io = convert_img(source, input_fmt=input_fmt, output_fmt=output_fmt)
            if self.profile:
                png_name = self.profile.handle
            else:
                png_name = token_hex(8)

            if tmpfile_io:
                converted_avatar = ContentFile(tmpfile_io.getvalue())
                converted_avatar.name = f'{png_name}.{output_fmt}'
                return converted_avatar
        except Exception as e:
            logger.error('Error: (%s) - Avatar PK: (%s)', str(e), self.id)

    def determine_response(self, use_svg=True):
        """Determine the content type and file to serve.

        Args:
            use_svg (bool): Whether or not to use SVG format.

        """
        if not use_svg:
            return self.png.file, 'image/png'
        else:
            return self.svg.file, 'image/svg+xml'


class CustomAvatar(BaseAvatar):
    recommended_by_staff = models.BooleanField(default=False)
    config = JSONField(default=dict, help_text=_('The JSON configuration.'))

    @classmethod
    def create(cls, profile, config_json):
        avatar = cls(
            profile=profile,
            config=config_json,
        )
        avatar.create_from_config()
        try:
            avatar_png = avatar.convert_field(avatar.svg, 'svg', 'png')
            avatar.png = avatar_png
            avatar.hash = BaseAvatar.calculate_hash(Image.open(BytesIO(avatar.png.read())))
            similar_avatar = avatar.find_similar()
            if similar_avatar:
                return similar_avatar
        except Exception as e:
            logger.warning("There was error during avatar conversion")
        return avatar

    def select(self, profile):
        new_avatar = CustomAvatar(profile=profile, config=self.config, svg=self.svg,
                                  png=self.png, hash=self.hash)
        similar_avatar = new_avatar.find_similar()
        if similar_avatar:
            return similar_avatar
        return new_avatar

    def create_from_config(self):
        """Create an avatar SVG from the configuration.

        TODO:
            * Deprecate in favor of request param based view using templates.

        """
        payload = self.config
        icon_width = self.ICON_SIZE[0]
        icon_height = self.ICON_SIZE[1]

        components = [
            icon_width, icon_height,
            Line([(0, icon_height / 2), (icon_width, icon_height / 2)],
                 width=f'{icon_height}px',
                 color=f"#{payload.get('Background')}")
        ]

        for k, v in payload.items():
            if k not in ['Background', 'ClothingColor', 'HairColor', 'SkinTone']:
                components.append(
                    build_avatar_component(f"{v.get('component_type')}/{v.get('svg_asset')}", self.ICON_SIZE)
                )

        with NamedTemporaryFile(mode='w+', suffix='.svg') as tmp:
            profile = None
            avatar = Figure(*components)
            avatar.save(tmp.name)
            with open(tmp.name) as file:
                if self.profile:
                    profile = self.profile

                svg_name = profile.handle if profile and profile.handle else token_hex(8)
                self.svg.save(f"{svg_name}.svg", File(file), save=False)

    def to_dict(self):
        return self.config


class SocialAvatar(BaseAvatar):

    @classmethod
    def github_avatar(cls, profile, avatar_img):
        avatar_hash = BaseAvatar.calculate_hash(avatar_img)
        avatar = cls(
            profile=profile,
            hash=avatar_hash
        )
        similar_avatar = avatar.find_similar()
        if similar_avatar:
            return similar_avatar
        avatar.png.save(f'{profile.handle}.png', ContentFile(get_temp_image_file(avatar_img).getvalue()), save=True)
        avatar.svg = avatar.convert_field(avatar.png, 'png', 'svg')
        return avatar
